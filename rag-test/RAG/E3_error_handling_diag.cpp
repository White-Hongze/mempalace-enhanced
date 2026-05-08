/**
 * E3: 带诊断发布的错误处理函数
 *
 * 设计思路：
 *   - 函数签名返回 bool 并接受诊断对象引用，职责清晰。
 *   - 错误日志携带文件名、行号、上下文字符串，可定位。
 *   - DiagnosticUpdater 发布 OK / WARN / ERROR 三级状态。
 *   - 可恢复错误 → 降级（返回 false 但不抛异常）；
 *     不可恢复错误 → 重新抛出让上层决策。
 */

#include <rclcpp/rclcpp.hpp>
#include <diagnostic_updater/diagnostic_updater.hpp>
#include <diagnostic_msgs/msg/diagnostic_status.hpp>

#include <stdexcept>
#include <string>

// =====================================================================
// 诊断状态聚合器：持有最近一次状态，供 updater 查询
// =====================================================================
struct DiagState {
    uint8_t     level{diagnostic_msgs::msg::DiagnosticStatus::OK};
    std::string message{"正常"};
};

// =====================================================================
// 核心处理函数
//
// @param input      输入数据（示例用整数）
// @param diag       诊断状态引用，函数内部写入
// @param logger     ROS 日志（可传 rclcpp::get_logger("...")）
// @return           true = 成功；false = 降级处理
// @throws           std::runtime_error  不可恢复错误（由调用方决定是否传播）
// =====================================================================
bool process_with_diagnostics(
    int                        input,
    DiagState&                 diag,
    const rclcpp::Logger&      logger)
{
    // ---- 防御性输入检查 ----
    if (input < 0) {
        // 可恢复：记录 WARN，降级返回
        RCLCPP_WARN(logger,
            "[%s:%d] 输入值 %d 超出预期范围，执行降级处理",
            __FILE__, __LINE__, input);

        diag.level   = diagnostic_msgs::msg::DiagnosticStatus::WARN;
        diag.message = "输入值超范围，已降级";
        return false;   // 降级：不继续处理
    }

    try {
        // ---- 业务逻辑（示例） ----
        if (input == 42) {
            // 模拟可恢复异常
            throw std::domain_error("遇到禁止值 42");
        }
        if (input > 1000) {
            // 模拟不可恢复异常
            throw std::overflow_error("数值溢出上限 1000");
        }

        // 正常路径
        diag.level   = diagnostic_msgs::msg::DiagnosticStatus::OK;
        diag.message = "处理正常";
        return true;

    } catch (const std::domain_error& e) {
        // 可恢复：记录上下文，降级
        RCLCPP_ERROR(logger,
            "[%s:%d] 域错误 (input=%d): %s — 执行降级",
            __FILE__, __LINE__, input, e.what());

        diag.level   = diagnostic_msgs::msg::DiagnosticStatus::WARN;
        diag.message = std::string("域错误降级: ") + e.what();
        return false;

    } catch (const std::overflow_error& e) {
        // 不可恢复：记录 ERROR 后重新抛出
        RCLCPP_FATAL(logger,
            "[%s:%d] 溢出错误 (input=%d): %s — 传播异常",
            __FILE__, __LINE__, input, e.what());

        diag.level   = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
        diag.message = std::string("致命溢出: ") + e.what();
        throw;  // 传播给调用方
    }
}

// =====================================================================
// ROS2 节点：集成 DiagnosticUpdater
// =====================================================================
class DiagNode : public rclcpp::Node {
public:
    DiagNode() : Node("diag_node"), updater_(this) {
        updater_.setHardwareID("sensor_board_v1");

        // 注册诊断任务：每次 updater_.force_update() 时被调用
        updater_.add("processing_status",
            [this](diagnostic_updater::DiagnosticStatusWrapper& stat) {
                stat.summary(diag_state_.level, diag_state_.message);
            });

        timer_ = create_wall_timer(
            std::chrono::milliseconds(100),
            [this]() { on_timer(); });
    }

private:
    void on_timer() {
        int input = counter_++;

        bool ok = false;
        try {
            ok = process_with_diagnostics(input, diag_state_, get_logger());
        } catch (const std::exception& e) {
            RCLCPP_ERROR(get_logger(), "节点层捕获不可恢复错误: %s", e.what());
            // 此处可选择重启节点逻辑
        }

        updater_.force_update();  // 立即发布诊断

        if (!ok) {
            RCLCPP_INFO(get_logger(), "本次处理已降级，跳过后续步骤");
        }
    }

    DiagState                            diag_state_;
    diagnostic_updater::DiagnosticUpdater updater_;
    rclcpp::TimerBase::SharedPtr         timer_;
    int                                  counter_{0};
};

// =====================================================================
// main
// =====================================================================
int main(int argc, char* argv[]) {
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<DiagNode>());
    rclcpp::shutdown();
    return 0;
}
