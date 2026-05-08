/**
 * E2 - ROS2 订阅回调架构：高频图像处理节点
 *
 * 遵循规范：
 *   3.2.1 回调中禁止长时间阻塞（回调只做轻量操作）
 *   6.1.1 不要在业务类内部直接 new 外部依赖（依赖注入）
 *   6.2.1 纯逻辑与 ROS 接口层分离
 *   4.1.2 参数更新应使用快照语义
 *   3.1.2 不要把所有逻辑写在回调里
 */

#pragma once

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <std_msgs/msg/float64.hpp>

#include <memory>
#include <mutex>
#include <thread>
#include <atomic>
#include <condition_variable>

// =====================================================================
// 1. 配置快照结构体（规范 4.1.2）
// =====================================================================
struct ImageProcessorConfig {
    double brightness_threshold{128.0};
    int blur_kernel_size{5};
    bool enable_edge_detection{true};
};

// =====================================================================
// 2. 纯逻辑处理器 —— 不依赖 ROS2 Node（规范 6.2.1 / 6.1.1）
// =====================================================================

/** 时钟接口（用于依赖注入，规范 6.1.1） */
class IClock {
public:
    virtual ~IClock() = default;
    virtual int64_t NowNs() const = 0;
};

/** 日志接口（用于依赖注入，规范 6.1.1） */
class ILogger {
public:
    virtual ~ILogger() = default;
    virtual void LogInfo(const std::string& msg) const = 0;
    virtual void LogWarn(const std::string& msg) const = 0;
};

/**
 * ImageProcessor —— 纯算法处理器，与 ROS 完全无关（规范 6.2.1）。
 * 所有外部依赖通过构造函数注入（规范 6.1.1）。
 */
class ImageProcessor {
public:
    /** 处理结果（显式返回，规范 6.2.2） */
    struct Result {
        double score{0.0};
        bool valid{false};
        int64_t process_time_ns{0};
    };

    ImageProcessor(std::shared_ptr<IClock> clock,
                   std::shared_ptr<ILogger> logger)
        : clock_(std::move(clock)), logger_(std::move(logger)) {}

    /**
     * 核心处理函数。
     * 接收原始数据和配置快照，显式返回结果（规范 6.2.2），无隐式副作用。
     */
    Result Process(const uint8_t* data, size_t size,
                   const ImageProcessorConfig& config) const {
        int64_t t0 = clock_->NowNs();

        // 检查输入合法性
        if (data == nullptr || size == 0) {
            logger_->LogWarn("ImageProcessor: empty input");
            return {0.0, false, 0};
        }

        // 模拟算法处理
        double sum = 0.0;
        for (size_t i = 0; i < size; ++i) {
            sum += data[i];
        }
        double mean = sum / static_cast<double>(size);

        bool valid = (mean >= config.brightness_threshold);
        double score = mean / 255.0;

        int64_t elapsed = clock_->NowNs() - t0;
        return {score, valid, elapsed};
    }

private:
    std::shared_ptr<IClock> clock_;
    std::shared_ptr<ILogger> logger_;
};

// =====================================================================
// 3. ROS2 节点 —— 薄接口层（规范 6.2.1 / 3.2.1 / 3.1.2）
// =====================================================================

class ImageProcessingNode : public rclcpp::Node {
public:
    ImageProcessingNode(std::shared_ptr<ImageProcessor> processor)
        : Node("image_processing_node"),
          processor_(std::move(processor)),
          running_(true) {
        // 声明参数
        declare_parameter("brightness_threshold", 128.0);
        declare_parameter("blur_kernel_size", 5);
        declare_parameter("enable_edge_detection", true);

        // 初始化配置快照（规范 4.1.2）
        RefreshConfigSnapshot();

        // 参数变化回调 —— 生成新快照
        param_cb_ = add_on_set_parameters_callback(
            [this](const std::vector<rclcpp::Parameter>&) {
                RefreshConfigSnapshot();
                rcl_interfaces::msg::SetParametersResult result;
                result.successful = true;
                return result;
            });

        // 订阅（QoS depth=1，最新值语义）
        sub_ = create_subscription<sensor_msgs::msg::Image>(
            "image_raw", rclcpp::SensorDataQoS(),
            [this](sensor_msgs::msg::Image::SharedPtr msg) {
                ImageCallback(msg);
            });

        // 发布
        pub_ = create_publisher<std_msgs::msg::Float64>("image_score", 10);

        // 后台处理线程
        worker_ = std::thread(&ImageProcessingNode::WorkerLoop, this);
    }

    ~ImageProcessingNode() override {
        running_ = false;
        cv_.notify_all();
        if (worker_.joinable()) worker_.join();
    }

private:
    /**
     * 回调函数 —— 只做轻量操作（规范 3.2.1 / 3.1.2）：
     *   1. 数据校验
     *   2. 存入最新帧（最新值覆盖，不排队）
     *   3. 通知工作线程
     */
    void ImageCallback(sensor_msgs::msg::Image::SharedPtr msg) {
        if (msg->data.empty()) return;

        {
            std::lock_guard<std::mutex> lock(frame_mutex_);
            latest_frame_ = msg;  // 最新值覆盖
        }
        cv_.notify_one();
    }

    /** 后台工作线程，承担计算密集处理 */
    void WorkerLoop() {
        while (running_) {
            sensor_msgs::msg::Image::SharedPtr frame;
            {
                std::unique_lock<std::mutex> lock(frame_mutex_);
                cv_.wait(lock, [this] {
                    return latest_frame_ != nullptr || !running_;
                });
                if (!running_) break;
                frame = std::exchange(latest_frame_, nullptr);
            }

            // 读取配置快照（规范 4.1.2）
            auto config = GetConfigSnapshot();

            // 调用纯逻辑处理器（规范 6.2.1）
            auto result = processor_->Process(
                frame->data.data(), frame->data.size(), config);

            // 发布结果（在锁外，规范 2.1.1）
            if (result.valid) {
                std_msgs::msg::Float64 out;
                out.data = result.score;
                pub_->publish(out);
            }
        }
    }

    /** 生成参数快照（规范 4.1.2） */
    void RefreshConfigSnapshot() {
        ImageProcessorConfig cfg;
        cfg.brightness_threshold = get_parameter("brightness_threshold").as_double();
        cfg.blur_kernel_size = get_parameter("blur_kernel_size").as_int();
        cfg.enable_edge_detection = get_parameter("enable_edge_detection").as_bool();

        std::lock_guard<std::mutex> lock(config_mutex_);
        config_snapshot_ = cfg;
    }

    ImageProcessorConfig GetConfigSnapshot() const {
        std::lock_guard<std::mutex> lock(config_mutex_);
        return config_snapshot_;
    }

    // 依赖注入的处理器（规范 6.1.1）
    std::shared_ptr<ImageProcessor> processor_;

    // 配置快照（规范 4.1.2）
    mutable std::mutex config_mutex_;
    ImageProcessorConfig config_snapshot_;
    rclcpp::node_interfaces::OnSetParametersCallbackHandle::SharedPtr param_cb_;

    // 最新帧缓存
    std::mutex frame_mutex_;
    std::condition_variable cv_;
    sensor_msgs::msg::Image::SharedPtr latest_frame_;

    // 工作线程
    std::atomic<bool> running_;
    std::thread worker_;

    // ROS 接口
    rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr sub_;
    rclcpp::Publisher<std_msgs::msg::Float64>::SharedPtr pub_;
};
