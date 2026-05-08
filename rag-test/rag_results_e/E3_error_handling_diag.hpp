/**
 * E3 - 错误处理与诊断
 *
 * 遵循规范：
 *   5.1.1 禁止吞掉异常
 *   5.1.2 错误日志要携带可定位信息（frame_id, request_id, 状态机阶段, 错误码）
 *   5.2.1 诊断状态与业务状态分离（INIT, WAITING, RUNNING, DEGRADED, ERROR, STOPPED）
 *   5.2.2 诊断必须可解释（状态描述、进入原因、影响范围、恢复条件）
 */

#pragma once

#include <string>
#include <cstdint>
#include <functional>
#include <sstream>
#include <chrono>

// =====================================================================
// 1. 诊断状态枚举（规范 5.2.1）
// =====================================================================
enum class DiagState {
    INIT,       // 初始化中
    WAITING,    // 等待输入/依赖
    RUNNING,    // 正常运行
    DEGRADED,   // 降级运行
    ERROR,      // 错误
    STOPPED     // 已停止
};

inline const char* DiagStateToStr(DiagState s) {
    switch (s) {
        case DiagState::INIT:     return "INIT";
        case DiagState::WAITING:  return "WAITING";
        case DiagState::RUNNING:  return "RUNNING";
        case DiagState::DEGRADED: return "DEGRADED";
        case DiagState::ERROR:    return "ERROR";
        case DiagState::STOPPED:  return "STOPPED";
    }
    return "UNKNOWN";
}

// =====================================================================
// 2. 诊断信息结构体（规范 5.2.2 —— 可解释性）
// =====================================================================
struct DiagInfo {
    DiagState state{DiagState::INIT};
    std::string description;       // 当前状态描述
    std::string reason;            // 进入原因
    std::string impact;            // 影响范围
    std::string recovery;          // 恢复条件
    int64_t timestamp_ns{0};       // 状态更新时间

    std::string ToString() const {
        std::ostringstream os;
        os << "state=" << DiagStateToStr(state)
           << " desc=\"" << description << "\""
           << " reason=\"" << reason << "\""
           << " impact=\"" << impact << "\""
           << " recovery=\"" << recovery << "\"";
        return os.str();
    }
};

// =====================================================================
// 3. 日志接口（用于依赖注入）
// =====================================================================
class IDiagLogger {
public:
    virtual ~IDiagLogger() = default;
    virtual void Error(const std::string& msg) = 0;
    virtual void Warn(const std::string& msg) = 0;
    virtual void Info(const std::string& msg) = 0;
};

// =====================================================================
// 4. 诊断发布接口
// =====================================================================
class IDiagPublisher {
public:
    virtual ~IDiagPublisher() = default;
    virtual void Publish(const DiagInfo& info) = 0;
};

// =====================================================================
// 5. 处理上下文（规范 5.1.2 —— 携带可定位信息）
// =====================================================================
struct ProcessContext {
    int frame_id{0};
    std::string request_id;
    std::string stage;  // 状态机阶段
};

// =====================================================================
// 6. 处理结果
// =====================================================================
struct ProcessResult {
    bool success{false};
    int error_code{0};
    std::string error_message;
    double output_value{0.0};
};

// =====================================================================
// 7. 错误处理与诊断管理器
// =====================================================================
class ErrorHandlingProcessor {
public:
    ErrorHandlingProcessor(std::shared_ptr<IDiagLogger> logger,
                           std::shared_ptr<IDiagPublisher> diag_pub)
        : logger_(std::move(logger)),
          diag_pub_(std::move(diag_pub)) {
        // 初始诊断状态
        UpdateDiag(DiagState::INIT,
                   "processor initializing",
                   "startup",
                   "no output",
                   "wait for first valid input");
    }

    /**
     * 主处理函数。
     * 显式返回 ProcessResult（规范 6.2.2），不隐式修改成员。
     *
     * 错误处理策略：
     *   - 记录上下文信息（规范 5.1.2）
     *   - 更新诊断状态（规范 5.2.1）
     *   - 决定降级或传播（规范 5.1.1 —— 不吞异常）
     */
    ProcessResult Execute(const ProcessContext& ctx, double input) {
        // 进入 RUNNING 状态
        UpdateDiag(DiagState::RUNNING,
                   "processing frame",
                   "new input received",
                   "none",
                   "N/A");

        // --- 输入校验 ---
        if (input < 0.0) {
            // 规范 5.1.2：错误日志携带 frame_id、request_id、阶段
            LogError(ctx, "VALIDATE", -1, "negative input value");

            // 规范 5.2.1/5.2.2：更新诊断状态
            UpdateDiag(DiagState::DEGRADED,
                       "received invalid input, using fallback",
                       "input < 0 at frame " + std::to_string(ctx.frame_id),
                       "output quality reduced",
                       "provide valid non-negative input");

            // 降级策略：使用默认值而非失败
            return {true, 0, "", 0.0};
        }

        // --- 核心处理 ---
        double result = 0.0;
        try {
            result = CoreCompute(input);
        } catch (const std::exception& e) {
            // 规范 5.1.1：禁止吞掉异常，必须记录并传播决策
            LogError(ctx, "COMPUTE", -2,
                     std::string("exception in CoreCompute: ") + e.what());

            UpdateDiag(DiagState::ERROR,
                       "compute failed with exception",
                       std::string("exception: ") + e.what(),
                       "no output for frame " + std::to_string(ctx.frame_id),
                       "fix input data or algorithm parameters");

            // 传播错误，不吞掉
            return {false, -2, e.what(), 0.0};
        }

        // --- 结果校验 ---
        if (result > 1e6) {
            LogError(ctx, "POSTCHECK", -3, "result overflow: " + std::to_string(result));

            UpdateDiag(DiagState::DEGRADED,
                       "result out of bounds, clamped",
                       "computed value > 1e6 at frame " + std::to_string(ctx.frame_id),
                       "downstream may receive clamped data",
                       "check sensor calibration");

            result = 1e6;  // 降级：截断
        }

        // 正常完成
        UpdateDiag(DiagState::RUNNING,
                   "processing OK",
                   "frame " + std::to_string(ctx.frame_id) + " completed",
                   "none",
                   "N/A");

        return {true, 0, "", result};
    }

private:
    /**
     * 规范 5.1.2：错误日志必须携带可定位信息。
     * 包含 frame_id、request_id、阶段、错误码。
     */
    void LogError(const ProcessContext& ctx, const std::string& stage,
                  int error_code, const std::string& detail) {
        std::ostringstream os;
        os << "[ERROR] frame_id=" << ctx.frame_id
           << " request_id=" << ctx.request_id
           << " stage=" << stage
           << " error_code=" << error_code
           << " detail=\"" << detail << "\"";
        logger_->Error(os.str());
    }

    /**
     * 规范 5.2.1/5.2.2：更新诊断，包含状态、描述、原因、影响、恢复条件。
     */
    void UpdateDiag(DiagState state, const std::string& desc,
                    const std::string& reason, const std::string& impact,
                    const std::string& recovery) {
        DiagInfo info;
        info.state = state;
        info.description = desc;
        info.reason = reason;
        info.impact = impact;
        info.recovery = recovery;
        info.timestamp_ns =
            std::chrono::steady_clock::now().time_since_epoch().count();

        diag_pub_->Publish(info);
    }

    double CoreCompute(double input) const {
        // 示例算法
        return input * 2.5 + 1.0;
    }

    std::shared_ptr<IDiagLogger> logger_;
    std::shared_ptr<IDiagPublisher> diag_pub_;
};
