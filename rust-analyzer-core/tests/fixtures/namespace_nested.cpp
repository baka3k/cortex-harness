// Nested namespaces + type alias + using declarations.
#include <string>

namespace outer {
namespace inner {

using StringVec = std::vector<std::string>;

class Logger {
public:
    void log(const std::string& msg);
    void set_level(int level);
};

void Logger::log(const std::string& msg) {
    log_impl(msg, 0);
}

void Logger::set_level(int level_) {
    level = level_;
}

}  // namespace inner
}  // namespace outer

namespace oi = outer::inner;

void use_logger() {
    oi::Logger logger;
    logger.log("hello");
    logger.set_level(2);
}
