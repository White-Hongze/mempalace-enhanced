/**
 * E5 - 可测试的算法类 + 单元测试样例
 *
 * 遵循规范：
 *   6.1.1 不要在业务类内部直接 new 外部依赖（构造函数依赖注入）
 *   6.2.1 纯逻辑与 ROS 接口层分离（不依赖 Node）
 *   6.2.2 显式返回结果，不修改成员变量（无隐式副作用）
 *   6.1   依赖注入（可 mock、可替换、隔离测试）
 */

#pragma once

#include <memory>
#include <vector>
#include <cmath>
#include <cstdint>
#include <string>

// =====================================================================
// 1. 外部依赖接口（规范 6.1.1 —— 可 mock）
// =====================================================================

class IClock {
public:
    virtual ~IClock() = default;
    virtual int64_t NowNs() const = 0;
};

class ILogger {
public:
    virtual ~ILogger() = default;
    virtual void Info(const std::string& msg) = 0;
    virtual void Warn(const std::string& msg) = 0;
};

// =====================================================================
// 2. 配置结构体（按值传入，快照语义）
// =====================================================================

struct FilterConfig {
    double alpha{0.5};          // 滤波系数
    double outlier_threshold{3.0};  // 离群值标准差倍数
    int min_samples{3};
};

// =====================================================================
// 3. 纯算法处理器（规范 6.2.1 / 6.1.1 / 6.2.2）
//    - 不依赖 ROS2 Node
//    - 所有外部依赖通过构造函数注入
//    - 处理函数显式返回结果
// =====================================================================

class DataFilterProcessor {
public:
    /** 处理结果（规范 6.2.2 —— 显式返回） */
    struct FilterResult {
        double filtered_value{0.0};
        bool is_outlier{false};
        int samples_used{0};
        int64_t process_time_ns{0};
    };

    /**
     * 构造函数依赖注入（规范 6.1.1）。
     * 不在内部 new 任何外部依赖。
     */
    DataFilterProcessor(std::shared_ptr<IClock> clock,
                        std::shared_ptr<ILogger> logger)
        : clock_(std::move(clock)), logger_(std::move(logger)) {}

    /**
     * 核心处理函数。
     * - 接收数据和配置，显式返回 FilterResult（规范 6.2.2）
     * - 不修改任何成员变量，无隐式副作用
     * - 纯逻辑，不依赖 ROS 接口（规范 6.2.1）
     */
    FilterResult Process(const std::vector<double>& samples,
                         const FilterConfig& config) const {
        int64_t t0 = clock_->NowNs();

        if (static_cast<int>(samples.size()) < config.min_samples) {
            logger_->Warn("insufficient samples: " +
                          std::to_string(samples.size()));
            return {0.0, false, 0, clock_->NowNs() - t0};
        }

        // 计算均值和标准差
        double sum = 0.0;
        for (double v : samples) sum += v;
        double mean = sum / samples.size();

        double sq_sum = 0.0;
        for (double v : samples) sq_sum += (v - mean) * (v - mean);
        double stddev = std::sqrt(sq_sum / samples.size());

        // 过滤离群值
        double filtered_sum = 0.0;
        int count = 0;
        bool any_outlier = false;

        for (double v : samples) {
            if (stddev > 0 &&
                std::abs(v - mean) > config.outlier_threshold * stddev) {
                any_outlier = true;
                continue;
            }
            filtered_sum += v;
            count++;
        }

        double result = (count > 0) ? (filtered_sum / count) : mean;

        // 指数加权（alpha 滤波）
        result = config.alpha * result + (1.0 - config.alpha) * mean;

        int64_t elapsed = clock_->NowNs() - t0;
        return {result, any_outlier, count, elapsed};
    }

private:
    std::shared_ptr<IClock> clock_;
    std::shared_ptr<ILogger> logger_;
};

// =====================================================================
// 4. 单元测试样例（GTest 框架）
// =====================================================================
#ifdef ENABLE_TESTS

#include <gtest/gtest.h>
#include <gmock/gmock.h>

// Mock 依赖（规范 6.1.1 —— 可 mock / 可替换）
class MockClock : public IClock {
public:
    MOCK_METHOD(int64_t, NowNs, (), (const, override));
};

class MockLogger : public ILogger {
public:
    MOCK_METHOD(void, Info, (const std::string&), (override));
    MOCK_METHOD(void, Warn, (const std::string&), (override));
};

class DataFilterProcessorTest : public ::testing::Test {
protected:
    void SetUp() override {
        mock_clock_ = std::make_shared<MockClock>();
        mock_logger_ = std::make_shared<MockLogger>();

        // 构造函数注入（规范 6.1.1）
        processor_ = std::make_unique<DataFilterProcessor>(
            mock_clock_, mock_logger_);
    }

    std::shared_ptr<MockClock> mock_clock_;
    std::shared_ptr<MockLogger> mock_logger_;
    std::unique_ptr<DataFilterProcessor> processor_;
    FilterConfig default_config_;
};

// 测试：正常输入
TEST_F(DataFilterProcessorTest, NormalInput) {
    EXPECT_CALL(*mock_clock_, NowNs())
        .WillRepeatedly(::testing::Return(0));

    std::vector<double> samples = {1.0, 2.0, 3.0, 4.0, 5.0};
    auto result = processor_->Process(samples, default_config_);

    EXPECT_TRUE(result.samples_used > 0);
    EXPECT_FALSE(result.is_outlier);
    EXPECT_NEAR(result.filtered_value, 3.0, 0.5);
}

// 测试：样本不足时的行为
TEST_F(DataFilterProcessorTest, InsufficientSamples) {
    EXPECT_CALL(*mock_clock_, NowNs())
        .WillRepeatedly(::testing::Return(0));
    EXPECT_CALL(*mock_logger_, Warn(::testing::_)).Times(1);

    std::vector<double> samples = {1.0};
    auto result = processor_->Process(samples, default_config_);

    EXPECT_EQ(result.samples_used, 0);
    EXPECT_DOUBLE_EQ(result.filtered_value, 0.0);
}

// 测试：离群值检测
TEST_F(DataFilterProcessorTest, OutlierDetection) {
    EXPECT_CALL(*mock_clock_, NowNs())
        .WillRepeatedly(::testing::Return(0));

    std::vector<double> samples = {1.0, 1.1, 1.0, 0.9, 100.0};
    auto result = processor_->Process(samples, default_config_);

    EXPECT_TRUE(result.is_outlier);
    EXPECT_LT(result.filtered_value, 10.0);  // 离群值应被过滤
}

// 测试：空输入
TEST_F(DataFilterProcessorTest, EmptyInput) {
    EXPECT_CALL(*mock_clock_, NowNs())
        .WillRepeatedly(::testing::Return(0));
    EXPECT_CALL(*mock_logger_, Warn(::testing::_)).Times(1);

    std::vector<double> samples;
    auto result = processor_->Process(samples, default_config_);

    EXPECT_EQ(result.samples_used, 0);
}

// 测试：自定义配置
TEST_F(DataFilterProcessorTest, CustomConfig) {
    EXPECT_CALL(*mock_clock_, NowNs())
        .WillRepeatedly(::testing::Return(0));

    FilterConfig config;
    config.alpha = 1.0;  // 完全信任滤波结果
    config.outlier_threshold = 1.0;  // 严格离群判定
    config.min_samples = 2;

    std::vector<double> samples = {10.0, 10.0, 10.0, 50.0};
    auto result = processor_->Process(samples, config);

    EXPECT_TRUE(result.is_outlier);
    EXPECT_NEAR(result.filtered_value, 10.0, 1.0);
}

#endif  // ENABLE_TESTS
