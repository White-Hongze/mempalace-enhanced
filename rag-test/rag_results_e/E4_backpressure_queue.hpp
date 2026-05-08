/**
 * E4 - 队列与背压设计：Producer-Consumer 模式
 *
 * 遵循规范：
 *   7.2   背压与队列管理（队列上限、溢出策略、丢包统计、诊断告警）
 *   3.3.2 明确"最新值语义"还是"全量事件语义"
 *   5.1.2 错误日志携带可定位信息
 *   5.2.2 诊断必须可解释
 */

#pragma once

#include <mutex>
#include <condition_variable>
#include <deque>
#include <atomic>
#include <functional>
#include <cstdint>
#include <string>
#include <sstream>
#include <chrono>
#include <optional>

// =====================================================================
// 1. 背压策略枚举（规范 7.2）
// =====================================================================
enum class OverflowPolicy {
    DROP_OLDEST,       // 丢弃最旧的
    DROP_NEWEST,       // 丢弃最新到达的
    OVERWRITE_LATEST   // 最新值覆盖（仅保留1条）
};

// =====================================================================
// 2. 队列统计信息（规范 7.2 —— 丢包统计与诊断告警）
// =====================================================================
struct QueueStats {
    uint64_t total_enqueued{0};
    uint64_t total_dequeued{0};
    uint64_t total_dropped{0};
    size_t current_size{0};
    size_t capacity{0};

    std::string ToString() const {
        std::ostringstream os;
        os << "enqueued=" << total_enqueued
           << " dequeued=" << total_dequeued
           << " dropped=" << total_dropped
           << " size=" << current_size
           << "/" << capacity;
        return os.str();
    }
};

// =====================================================================
// 3. 诊断告警回调接口（规范 7.2 / 5.2.2）
// =====================================================================
using DiagAlarmCallback = std::function<void(const std::string& message,
                                              const QueueStats& stats)>;

// =====================================================================
// 4. BackPressureQueue —— 带背压策略的有界队列
// =====================================================================
template <typename T>
class BackPressureQueue {
public:
    /**
     * @param capacity      队列容量上限（规范 7.2：必须有上限）
     * @param policy        溢出策略（规范 7.2：丢旧/丢新/最新值覆盖）
     * @param alarm_cb      诊断告警回调（规范 7.2）
     * @param alarm_threshold 告警阈值（队列占比，0.8 = 80%满时告警）
     */
    BackPressureQueue(size_t capacity,
                      OverflowPolicy policy,
                      DiagAlarmCallback alarm_cb = nullptr,
                      double alarm_threshold = 0.8)
        : capacity_(capacity),
          policy_(policy),
          alarm_cb_(std::move(alarm_cb)),
          alarm_threshold_(alarm_threshold) {}

    /**
     * Producer 调用：投递数据。
     * 当队列满时按策略处理，统计丢包数（规范 7.2）。
     */
    void Push(T item) {
        std::lock_guard<std::mutex> lock(mutex_);

        stats_.total_enqueued++;

        if (queue_.size() >= capacity_) {
            // 队列已满，执行背压策略
            switch (policy_) {
                case OverflowPolicy::DROP_OLDEST:
                    queue_.pop_front();
                    queue_.push_back(std::move(item));
                    stats_.total_dropped++;
                    break;

                case OverflowPolicy::DROP_NEWEST:
                    // 丢弃当前到达的
                    stats_.total_dropped++;
                    break;

                case OverflowPolicy::OVERWRITE_LATEST:
                    // 清空队列，只保留最新一条
                    stats_.total_dropped += queue_.size();
                    queue_.clear();
                    queue_.push_back(std::move(item));
                    break;
            }
        } else {
            queue_.push_back(std::move(item));
        }

        stats_.current_size = queue_.size();

        // 诊断告警（规范 7.2 / 5.2.2）
        CheckAlarm();

        cv_.notify_one();
    }

    /**
     * Consumer 调用：取出数据（阻塞等待）。
     * 显式返回 optional（规范 6.2.2），shutdown 时返回 nullopt。
     */
    std::optional<T> Pop() {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [this] { return !queue_.empty() || shutdown_; });

        if (shutdown_ && queue_.empty()) {
            return std::nullopt;
        }

        T item = std::move(queue_.front());
        queue_.pop_front();
        stats_.total_dequeued++;
        stats_.current_size = queue_.size();

        return item;
    }

    /**
     * 非阻塞尝试取数据。
     */
    std::optional<T> TryPop() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (queue_.empty()) return std::nullopt;

        T item = std::move(queue_.front());
        queue_.pop_front();
        stats_.total_dequeued++;
        stats_.current_size = queue_.size();

        return item;
    }

    /** 获取当前统计快照（规范 7.2 —— 丢包统计） */
    QueueStats GetStats() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return stats_;
    }

    /** 关闭队列，唤醒所有等待者 */
    void Shutdown() {
        std::lock_guard<std::mutex> lock(mutex_);
        shutdown_ = true;
        cv_.notify_all();
    }

    size_t Size() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return queue_.size();
    }

private:
    /**
     * 当队列占用超过阈值时触发诊断告警（规范 7.2 / 5.2.2）。
     * 告警信息包含：状态、原因、影响范围、当前统计。
     */
    void CheckAlarm() {
        if (!alarm_cb_) return;

        double usage = static_cast<double>(queue_.size()) / capacity_;
        if (usage >= alarm_threshold_) {
            std::ostringstream os;
            os << "BackPressureQueue alarm: usage="
               << static_cast<int>(usage * 100) << "%, "
               << "dropped=" << stats_.total_dropped
               << " reason=producer_faster_than_consumer"
               << " impact=data_loss_possible"
               << " recovery=increase_consumer_throughput_or_reduce_producer_rate";
            alarm_cb_(os.str(), stats_);
        }
    }

    const size_t capacity_;
    const OverflowPolicy policy_;
    const DiagAlarmCallback alarm_cb_;
    const double alarm_threshold_;

    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<T> queue_;
    QueueStats stats_;
    bool shutdown_{false};
};

// ======================== 使用示例 ========================
#ifdef ENABLE_EXAMPLE

#include <thread>
#include <iostream>

struct SensorFrame {
    int id;
    double data;
};

int main() {
    auto alarm = [](const std::string& msg, const QueueStats& stats) {
        std::cerr << "[DIAG] " << msg << " | " << stats.ToString() << std::endl;
    };

    // 有上限队列，丢旧策略，80%告警（规范 7.2）
    BackPressureQueue<SensorFrame> queue(100, OverflowPolicy::DROP_OLDEST, alarm, 0.8);

    // Producer
    std::thread producer([&queue] {
        for (int i = 0; i < 500; ++i) {
            queue.Push({i, i * 0.1});
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
        queue.Shutdown();
    });

    // Consumer（故意慢于 Producer）
    std::thread consumer([&queue] {
        while (auto frame = queue.Pop()) {
            // 处理
            std::this_thread::sleep_for(std::chrono::microseconds(500));
        }
    });

    producer.join();
    consumer.join();

    auto stats = queue.GetStats();
    std::cout << "Final: " << stats.ToString() << std::endl;
    return 0;
}

#endif  // ENABLE_EXAMPLE
