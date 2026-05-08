/**
 * E5: 可单元测试的算法类（不依赖 ROS2 Node 或任何外部系统）
 *
 * 设计思路：
 *   - DataFilter 类通过构造函数接受 Config，不持有任何全局状态。
 *   - process() 显式返回结果，无副作用，便于断言。
 *   - 依赖注入：时钟函数以 std::function 注入，测试时传入确定性时钟。
 *   - GTest 测试样例覆盖正常路径、边界、异常三种场景。
 */

// ============================================================
// 算法实现（data_filter.hpp / data_filter.cpp）
// ============================================================
#include <cmath>
#include <functional>
#include <stdexcept>
#include <vector>
#include <numeric>

// ---- 配置 ----
struct FilterConfig {
    double  cutoff_freq{10.0};   // 低通截止频率 (Hz)
    double  sample_rate{100.0};  // 采样率 (Hz)
    size_t  window_size{5};      // 移动平均窗口
};

// ---- 处理结果 ----
struct FilterResult {
    double  smoothed_value;          // 低通滤波后的值
    double  moving_average;          // 移动平均
    bool    above_threshold{false};  // 是否超过阈值
};

// ---- 算法类 ----
class DataFilter {
public:
    using ClockFn = std::function<double()>;  // 返回当前时间戳（秒）

    /**
     * @param cfg      滤波参数
     * @param clock_fn 时钟函数（依赖注入，默认用 steady_clock）
     */
    explicit DataFilter(
        const FilterConfig& cfg,
        ClockFn clock_fn = nullptr)
        : cfg_(cfg)
    {
        if (clock_fn) {
            clock_ = std::move(clock_fn);
        } else {
            clock_ = []() -> double {
                using namespace std::chrono;
                return duration<double>(steady_clock::now().time_since_epoch()).count();
            };
        }

        // 一阶 RC 低通系数
        double rc = 1.0 / (2.0 * M_PI * cfg_.cutoff_freq);
        double dt = 1.0 / cfg_.sample_rate;
        alpha_    = dt / (rc + dt);

        if (cfg_.window_size == 0) {
            throw std::invalid_argument("window_size 必须 > 0");
        }
    }

    /**
     * 核心处理函数（无副作用，显式返回）。
     * @param raw_value  原始输入值
     * @param threshold  超阈值判断基准
     * @return           FilterResult
     */
    FilterResult process(double raw_value, double threshold = 0.0) {
        // 1) 一阶低通滤波
        lp_state_ = alpha_ * raw_value + (1.0 - alpha_) * lp_state_;

        // 2) 移动平均
        window_.push_back(raw_value);
        if (window_.size() > cfg_.window_size) {
            window_.erase(window_.begin());
        }
        double ma = std::accumulate(window_.begin(), window_.end(), 0.0)
                    / static_cast<double>(window_.size());

        return FilterResult{
            .smoothed_value  = lp_state_,
            .moving_average  = ma,
            .above_threshold = (lp_state_ > threshold),
        };
    }

    /** 重置内部状态 */
    void reset() {
        lp_state_ = 0.0;
        window_.clear();
    }

private:
    FilterConfig       cfg_;
    ClockFn            clock_;
    double             alpha_{0.0};
    double             lp_state_{0.0};
    std::vector<double> window_;
};


// ============================================================
// GTest 单元测试样例（通常放 test_data_filter.cpp）
// ============================================================
#include <gtest/gtest.h>

// ---- 夹具 ----
class DataFilterTest : public ::testing::Test {
protected:
    void SetUp() override {
        FilterConfig cfg;
        cfg.cutoff_freq  = 10.0;
        cfg.sample_rate  = 100.0;
        cfg.window_size  = 3;

        // 注入确定性时钟（从 0 开始，每次 +0.01 s）
        double fake_time = 0.0;
        filter_ = std::make_unique<DataFilter>(cfg, [fake_time]() mutable {
            return fake_time += 0.01;
        });
    }

    std::unique_ptr<DataFilter> filter_;
};

// ---- 测试：直流输入稳定收敛 ----
TEST_F(DataFilterTest, SteadyStateConverges) {
    FilterResult result;
    for (int i = 0; i < 100; ++i) {
        result = filter_->process(1.0);
    }
    // 经过足够多次后，低通输出应非常接近 1.0
    EXPECT_NEAR(result.smoothed_value, 1.0, 1e-3);
}

// ---- 测试：移动平均窗口大小为 3 ----
TEST_F(DataFilterTest, MovingAverageWindowSize3) {
    filter_->process(3.0);  // window=[3]
    filter_->process(6.0);  // window=[3,6]
    auto r = filter_->process(9.0);  // window=[3,6,9]

    EXPECT_DOUBLE_EQ(r.moving_average, 6.0);
}

// ---- 测试：超阈值标志 ----
TEST_F(DataFilterTest, AboveThresholdFlag) {
    // 先让滤波器收敛到接近 5.0
    for (int i = 0; i < 200; ++i) filter_->process(5.0);

    auto r_below = filter_->process(5.0, /*threshold=*/10.0);
    EXPECT_FALSE(r_below.above_threshold);

    auto r_above = filter_->process(5.0, /*threshold=*/1.0);
    EXPECT_TRUE(r_above.above_threshold);
}

// ---- 测试：重置后状态归零 ----
TEST_F(DataFilterTest, ResetClearsState) {
    filter_->process(100.0);
    filter_->process(100.0);
    filter_->reset();

    auto r = filter_->process(0.0);
    // 重置后首次输入 0，低通输出应为 0（初态 = 0）
    EXPECT_DOUBLE_EQ(r.smoothed_value, 0.0);
    EXPECT_DOUBLE_EQ(r.moving_average, 0.0);
}

// ---- 测试：非法 window_size 抛异常 ----
TEST(DataFilterConstructTest, ZeroWindowThrows) {
    FilterConfig bad_cfg;
    bad_cfg.window_size = 0;
    EXPECT_THROW(DataFilter(bad_cfg), std::invalid_argument);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
