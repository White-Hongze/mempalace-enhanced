/**
 * E2: ROS2 高频图像处理节点
 *
 * 设计思路：
 *   - 订阅回调仅做入队，保持轻量（< 1 µs）。
 *   - ImageProcessor 类独立于 ROS 接口，持有处理逻辑。
 *   - 独立工作线程从队列取帧处理，与订阅回调解耦。
 *   - 参数动态更新通过 parameter_event_handler 实现。
 */

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <rcl_interfaces/msg/set_parameters_result.hpp>

#include <atomic>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <queue>
#include <thread>

// =====================================================================
// 1) 处理器类：与 ROS 完全解耦，便于单元测试
// =====================================================================
struct ProcessorConfig {
    int    blur_kernel_size{5};
    double scale_factor{1.0};
};

class ImageProcessor {
public:
    explicit ImageProcessor(const ProcessorConfig& cfg) : cfg_(cfg) {}

    /** 更新配置（线程安全） */
    void update_config(const ProcessorConfig& cfg) {
        std::lock_guard<std::mutex> lk(cfg_mutex_);
        cfg_ = cfg;
    }

    /**
     * 核心处理函数。
     * 实际项目中替换为 OpenCV 操作，此处用日志示意。
     * @return 处理后的图像消息
     */
    sensor_msgs::msg::Image::SharedPtr process(
        const sensor_msgs::msg::Image::ConstSharedPtr& input)
    {
        ProcessorConfig cfg;
        {
            std::lock_guard<std::mutex> lk(cfg_mutex_);
            cfg = cfg_;
        }

        // TODO: 实际处理（高斯模糊、缩放等）
        // auto output = apply_blur(input, cfg.blur_kernel_size);
        // output      = apply_scale(output, cfg.scale_factor);

        auto output = std::make_shared<sensor_msgs::msg::Image>(*input);
        output->header.stamp = rclcpp::Clock().now();
        return output;
    }

private:
    ProcessorConfig      cfg_;
    mutable std::mutex   cfg_mutex_;
};

// =====================================================================
// 2) ROS2 节点：仅负责 I/O 与线程调度
// =====================================================================
class ImageProcNode : public rclcpp::Node {
public:
    explicit ImageProcNode(const rclcpp::NodeOptions& options = rclcpp::NodeOptions())
        : Node("image_proc_node", options)
    {
        // --- 声明参数 ---
        declare_parameter<int>("blur_kernel_size", 5);
        declare_parameter<double>("scale_factor", 1.0);

        // --- 初始化处理器 ---
        ProcessorConfig cfg;
        cfg.blur_kernel_size = get_parameter("blur_kernel_size").as_int();
        cfg.scale_factor     = get_parameter("scale_factor").as_double();
        processor_           = std::make_unique<ImageProcessor>(cfg);

        // --- 订阅图像 ---
        sub_ = create_subscription<sensor_msgs::msg::Image>(
            "input/image_raw", 10,
            [this](sensor_msgs::msg::Image::ConstSharedPtr msg) {
                enqueue(msg);
            });

        // --- 发布结果 ---
        pub_ = create_publisher<sensor_msgs::msg::Image>("output/image_proc", 10);

        // --- 参数动态更新回调 ---
        param_cb_ = add_on_set_parameters_callback(
            [this](const std::vector<rclcpp::Parameter>& params)
                -> rcl_interfaces::msg::SetParametersResult {
                return on_parameters_changed(params);
            });

        // --- 启动工作线程 ---
        worker_running_ = true;
        worker_ = std::thread(&ImageProcNode::worker_loop, this);
    }

    ~ImageProcNode() override {
        worker_running_ = false;
        cv_.notify_all();
        if (worker_.joinable()) worker_.join();
    }

private:
    // ---- 入队（回调，轻量） ----
    void enqueue(const sensor_msgs::msg::Image::ConstSharedPtr& msg) {
        constexpr size_t MAX_QUEUE = 5;
        std::lock_guard<std::mutex> lk(queue_mutex_);
        if (queue_.size() >= MAX_QUEUE) {
            queue_.pop();  // 背压：丢弃最旧帧
            RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 1000, "帧队列满，丢弃旧帧");
        }
        queue_.push(msg);
        cv_.notify_one();
    }

    // ---- 工作线程 ----
    void worker_loop() {
        while (worker_running_) {
            sensor_msgs::msg::Image::ConstSharedPtr frame;
            {
                std::unique_lock<std::mutex> lk(queue_mutex_);
                cv_.wait(lk, [this] { return !queue_.empty() || !worker_running_; });
                if (!worker_running_ && queue_.empty()) break;
                frame = queue_.front();
                queue_.pop();
            }

            auto result = processor_->process(frame);
            pub_->publish(*result);
        }
    }

    // ---- 参数变更处理 ----
    rcl_interfaces::msg::SetParametersResult on_parameters_changed(
        const std::vector<rclcpp::Parameter>& params)
    {
        ProcessorConfig cfg;
        cfg.blur_kernel_size = get_parameter("blur_kernel_size").as_int();
        cfg.scale_factor     = get_parameter("scale_factor").as_double();

        for (const auto& p : params) {
            if (p.get_name() == "blur_kernel_size")
                cfg.blur_kernel_size = p.as_int();
            else if (p.get_name() == "scale_factor")
                cfg.scale_factor = p.as_double();
        }

        processor_->update_config(cfg);

        rcl_interfaces::msg::SetParametersResult result;
        result.successful = true;
        return result;
    }

    // ---- 成员 ----
    rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr sub_;
    rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr    pub_;
    rclcpp::node_interfaces::OnSetParametersCallbackHandle::SharedPtr param_cb_;

    std::unique_ptr<ImageProcessor>                          processor_;

    std::queue<sensor_msgs::msg::Image::ConstSharedPtr>      queue_;
    std::mutex                                               queue_mutex_;
    std::condition_variable                                  cv_;
    std::thread                                              worker_;
    std::atomic<bool>                                        worker_running_{false};
};

// =====================================================================
// 3) main
// =====================================================================
int main(int argc, char* argv[]) {
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<ImageProcNode>());
    rclcpp::shutdown();
    return 0;
}
