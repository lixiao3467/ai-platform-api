# config/
# 本地配置文件目录 — Nacos 不可用时的降级读取源
#
# 加载优先级链：
#   环境变量 > Nacos > config/*.yaml > .env > 代码默认值
#
# 生产环境：将此目录替换为生产配置，或通过 Nacos 统一管理
