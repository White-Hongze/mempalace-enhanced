/**
 * E1 - 并发缓存更新：线程安全的数据缓存类
 *
 * 遵循规范：
 *   2.1.4 优先用 RAII 管理锁生命周期
 *   2.1.1 尽量缩小临界区
 *   2.2.1 多字段一致性通过快照实现（非多个原子变量拼装）
 *   6.2.2 显式返回而非隐式副作用
 *   4.1.2 参数更新应使用快照语义
 */

#pragma once

#include <mutex>
#include <memory>
#include <utility>

/**
 * CacheData —— 缓存数据快照结构体。
 * 所有相关字段聚合在一个结构体内，保证多字段一致性（规范 2.2.1）。
 */
struct CacheData {
    int frame_id{0};
    double position_x{0.0};
    double position_y{0.0};
    double velocity{0.0};
    int64_t timestamp_ns{0};
};

/**
 * ThreadSafeCache —— 线程安全的数据缓存类。
 *
 * 后台线程通过 Update() 写入新快照，
 * 前台线程通过 GetSnapshot() 获取一致性快照。
 */
class ThreadSafeCache {
public:
    /**
     * 后台线程调用：更新缓存数据。
     * 使用 RAII 管理锁（规范 2.1.4），临界区仅覆盖指针交换（规范 2.1.1）。
     *
     * @param new_data 新的缓存数据（按值传入，构造在锁外完成）
     */
    void Update(CacheData new_data) {
        // 在锁外完成数据构造，最小化临界区（规范 2.1.1）
        auto new_snapshot = std::make_shared<const CacheData>(std::move(new_data));

        // RAII 锁管理（规范 2.1.4），临界区仅做指针交换
        {
            std::lock_guard<std::mutex> lock(mutex_);
            snapshot_ = new_snapshot;
        }
    }

    /**
     * 前台线程调用：获取当前缓存的一致性快照。
     * 显式返回快照（规范 6.2.2），而非通过输出参数或修改成员。
     * 快照语义保证读到的所有字段来自同一次更新（规范 2.2.1 / 4.1.2）。
     *
     * @return 当前缓存快照的 shared_ptr；若从未更新过则返回 nullptr
     */
    std::shared_ptr<const CacheData> GetSnapshot() const {
        std::lock_guard<std::mutex> lock(mutex_);  // RAII（规范 2.1.4）
        return snapshot_;  // 拷贝 shared_ptr，临界区极小（规范 2.1.1）
    }

    /**
     * 判断缓存是否已初始化。
     * 显式返回 bool（规范 6.2.2）。
     */
    bool HasData() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return snapshot_ != nullptr;
    }

private:
    mutable std::mutex mutex_;

    // 使用 shared_ptr<const> 实现快照语义（规范 2.2.1）：
    // 写线程生成新 shared_ptr 并原子性替换；读线程拿到的旧 shared_ptr 仍然有效。
    std::shared_ptr<const CacheData> snapshot_;
};

// ======================== 使用示例 ========================
#ifdef ENABLE_EXAMPLE

#include <thread>
#include <chrono>
#include <iostream>

void BackgroundUpdater(ThreadSafeCache& cache) {
    for (int i = 0; i < 100; ++i) {
        CacheData data;
        data.frame_id = i;
        data.position_x = i * 1.0;
        data.position_y = i * 0.5;
        data.velocity = 10.0 + i * 0.1;
        data.timestamp_ns =
            std::chrono::steady_clock::now().time_since_epoch().count();

        cache.Update(std::move(data));
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}

void FrontendReader(const ThreadSafeCache& cache) {
    for (int i = 0; i < 50; ++i) {
        auto snap = cache.GetSnapshot();  // 显式返回快照（规范 6.2.2）
        if (snap) {
            std::cout << "frame=" << snap->frame_id
                      << " x=" << snap->position_x
                      << " y=" << snap->position_y
                      << " v=" << snap->velocity << "\n";
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
}

int main() {
    ThreadSafeCache cache;
    std::thread writer(BackgroundUpdater, std::ref(cache));
    std::thread reader(FrontendReader, std::cref(cache));
    writer.join();
    reader.join();
    return 0;
}

#endif  // ENABLE_EXAMPLE
