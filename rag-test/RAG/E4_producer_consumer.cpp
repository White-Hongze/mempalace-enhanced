/**
 * E4: Producer-Consumer 模式，含背压策略与统计指标
 *
 * 设计思路：
 *   - BoundedQueue<T>：有界阻塞队列，满时支持两种背压策略：
 *       BLOCK  — 生产者阻塞，直到有空间（适合不允许丢弃）
 *       DROP   — 丢弃最新数据并计数（适合实时性优先）
 *   - Stats：原子计数器，记录生产/消费/丢弃数量与延迟。
 *   - Producer / Consumer 各自独立线程，无耦合。
 */

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <iostream>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

// =====================================================================
// 统计指标
// =====================================================================
struct QueueStats {
    std::atomic<uint64_t> produced{0};   // 生产者成功入队次数
    std::atomic<uint64_t> consumed{0};   // 消费者出队次数
    std::atomic<uint64_t> dropped{0};    // 因队列满而丢弃次数
    std::atomic<uint64_t> total_wait_us{0}; // 消费端累计等待（微秒）

    void print() const {
        std::cout << "[Stats] produced=" << produced
                  << "  consumed="  << consumed
                  << "  dropped="   << dropped
                  << "  avg_wait_us="
                  << (consumed > 0 ? total_wait_us.load() / consumed.load() : 0)
                  << "\n";
    }
};

// =====================================================================
// 背压策略
// =====================================================================
enum class BackpressurePolicy { BLOCK, DROP };

// =====================================================================
// 有界阻塞队列
// =====================================================================
template <typename T>
class BoundedQueue {
public:
    explicit BoundedQueue(size_t capacity,
                          BackpressurePolicy policy = BackpressurePolicy::DROP)
        : capacity_(capacity), policy_(policy) {}

    /**
     * 生产者入队。
     * BLOCK 策略：满时阻塞直到有空间，或队列关闭。
     * DROP  策略：满时直接丢弃，更新统计并立即返回。
     * @return true = 成功入队；false = 已丢弃或队列已关闭
     */
    bool push(T item) {
        std::unique_lock<std::mutex> lk(mutex_);

        if (policy_ == BackpressurePolicy::BLOCK) {
            not_full_.wait(lk, [this] {
                return queue_.size() < capacity_ || !open_;
            });
            if (!open_) return false;
        } else {
            // DROP 策略
            if (queue_.size() >= capacity_) {
                ++stats_.dropped;
                return false;
            }
        }

        queue_.push_back(std::move(item));
        ++stats_.produced;
        not_empty_.notify_one();
        return true;
    }

    /**
     * 消费者出队（阻塞直到有数据或队列关闭）。
     * @return std::nullopt 表示队列已关闭且为空
     */
    std::optional<T> pop() {
        auto t0 = std::chrono::steady_clock::now();

        std::unique_lock<std::mutex> lk(mutex_);
        not_empty_.wait(lk, [this] {
            return !queue_.empty() || !open_;
        });

        if (queue_.empty()) return std::nullopt;

        auto item = std::move(queue_.front());
        queue_.pop_front();

        auto wait_us = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - t0).count();
        stats_.total_wait_us += static_cast<uint64_t>(wait_us);
        ++stats_.consumed;

        not_full_.notify_one();
        return item;
    }

    /** 关闭队列，唤醒所有等待线程 */
    void close() {
        std::lock_guard<std::mutex> lk(mutex_);
        open_ = false;
        not_empty_.notify_all();
        not_full_.notify_all();
    }

    size_t size() const {
        std::lock_guard<std::mutex> lk(mutex_);
        return queue_.size();
    }

    const QueueStats& stats() const { return stats_; }

private:
    const size_t           capacity_;
    const BackpressurePolicy policy_;
    bool                   open_{true};

    mutable std::mutex         mutex_;
    std::condition_variable    not_empty_;
    std::condition_variable    not_full_;
    std::deque<T>              queue_;
    QueueStats                 stats_;
};

// =====================================================================
// 生产者
// =====================================================================
class Producer {
public:
    Producer(BoundedQueue<int>& q, int total, std::chrono::milliseconds interval)
        : queue_(q), total_(total), interval_(interval) {}

    void run() {
        for (int i = 0; i < total_; ++i) {
            bool ok = queue_.push(i);
            if (!ok) {
                // DROP 策略下可能返回 false
            }
            std::this_thread::sleep_for(interval_);
        }
        queue_.close();
    }

private:
    BoundedQueue<int>&        queue_;
    int                       total_;
    std::chrono::milliseconds interval_;
};

// =====================================================================
// 消费者
// =====================================================================
class Consumer {
public:
    explicit Consumer(BoundedQueue<int>& q, std::chrono::milliseconds process_time)
        : queue_(q), process_time_(process_time) {}

    void run() {
        while (true) {
            auto item = queue_.pop();
            if (!item.has_value()) break;  // 队列已关闭且为空
            // 模拟耗时处理
            std::this_thread::sleep_for(process_time_);
        }
    }

private:
    BoundedQueue<int>&        queue_;
    std::chrono::milliseconds process_time_;
};

// =====================================================================
// 验证
// =====================================================================
int main() {
    // 场景：生产 5ms/item，消费 20ms/item → 队列必然累积 → 测试背压
    BoundedQueue<int> queue(8, BackpressurePolicy::DROP);

    Producer producer(queue, 50, std::chrono::milliseconds(5));
    Consumer consumer(queue, std::chrono::milliseconds(20));

    std::thread t_prod([&] { producer.run(); });
    std::thread t_cons([&] { consumer.run(); });

    t_prod.join();
    t_cons.join();

    queue.stats().print();
    return 0;
}
