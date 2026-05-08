/**
 * E1: 线程安全的数据缓存类
 *
 * 设计思路：
 *   - 使用"双缓冲 + 读写锁"方案，保证多字段一致性。
 *   - 后台线程写入 pending_ 副本后，原子交换为 active_，前台只读 active_。
 *   - shared_mutex 允许多个前台线程并发读，写时独占。
 */

#include <atomic>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <thread>
#include <chrono>
#include <iostream>

// ---- 数据快照（多字段，整体一致） ----
struct SensorSnapshot {
    double   temperature{0.0};
    double   pressure{0.0};
    uint64_t timestamp_ns{0};
};

// ---- 线程安全缓存 ----
class ThreadSafeCache {
public:
    ThreadSafeCache() : running_(false) {}

    ~ThreadSafeCache() { stop(); }

    /** 启动后台更新线程 */
    void start() {
        running_ = true;
        update_thread_ = std::thread(&ThreadSafeCache::update_loop, this);
    }

    /** 停止后台线程 */
    void stop() {
        running_ = false;
        if (update_thread_.joinable()) {
            update_thread_.join();
        }
    }

    /**
     * 前台 API：读取最新一致快照（共享锁，允许多读者并发）
     */
    SensorSnapshot get_snapshot() const {
        std::shared_lock<std::shared_mutex> lock(mutex_);
        return *active_;
    }

    /**
     * 后台 API：原子替换快照（独占锁）
     * 先在锁外构建新数据，减少持锁时间。
     */
    void update_snapshot(const SensorSnapshot& new_data) {
        auto new_snapshot = std::make_shared<SensorSnapshot>(new_data);
        std::unique_lock<std::shared_mutex> lock(mutex_);
        active_ = std::move(new_snapshot);
    }

private:
    void update_loop() {
        uint64_t tick = 0;
        while (running_) {
            // 模拟数据采集（实际应替换为真实传感器读取）
            SensorSnapshot snap;
            snap.temperature  = 20.0 + tick * 0.01;
            snap.pressure     = 101325.0 - tick * 0.1;
            snap.timestamp_ns = static_cast<uint64_t>(
                std::chrono::steady_clock::now().time_since_epoch().count());

            update_snapshot(snap);
            ++tick;

            std::this_thread::sleep_for(std::chrono::milliseconds(10)); // 100 Hz
        }
    }

    mutable std::shared_mutex             mutex_;
    std::shared_ptr<SensorSnapshot>       active_{std::make_shared<SensorSnapshot>()};
    std::thread                           update_thread_;
    std::atomic<bool>                     running_;
};

// ---- 简单验证 ----
int main() {
    ThreadSafeCache cache;
    cache.start();

    for (int i = 0; i < 5; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        auto snap = cache.get_snapshot();
        std::cout << "temp=" << snap.temperature
                  << "  pressure=" << snap.pressure
                  << "  ts=" << snap.timestamp_ns << "\n";
    }

    cache.stop();
    return 0;
}
