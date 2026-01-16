# NJU Grade Watcher (南京大学成绩自动推送脚本)

这是一个基于 Python 的南京大学教务系统成绩监控脚本。它能够自动登录 eHall 大厅，轮询成绩接口，并在发现新出成绩时通过 Server酱 (ServerChan) 推送通知到你的手机（微信/App）。

## ✨ 功能特性

* **自动登录**：集成 `NJUlogin`，支持自动处理登录验证码。
* **增量推送**：启动时自动记录已有课程，仅当检测到新的课程号 (KCH) 时才发送通知，避免重复打扰。
* **即时通知**：支持 ServerChan 推送，第一时间获取成绩动态。
* **防掉线机制**：内置 Session 保持和自动重试逻辑。如果 Cookie 失效或网络波动，脚本会自动尝试重新登录。
* **随机轮询**：采用随机间隔（10s - 120s）进行查询，降低被服务器风控的风险。

## 🛠️ 依赖环境

* Python 3.x
* [NJUlogin](https://github.com/Do1e/NJUlogin)
* requests
* serverchan-sdk

## 🚀 快速开始

### 1. 安装依赖

确保你已经安装了必要的 Python 库。

```bash
pip install requests serverchan-sdk NJUlogin
# 注意：你需要确保 NJUlogin 模块在当前目录下，或者已安装到环境中
```

### 2. 配置脚本

打开 `fetch.py`，找到顶部的配置区域，填入你的个人信息：

**Python**

```
# ===================== 你需要自定义的配置 =====================
USERNAME = "你的学号"
PASSWORD = "你的统一身份认证密码"
SENDKEY = "你的Server酱Key"  # sct.ftqq.com 和 sc3.ft07.com 都可以用
# ============================================================
```

### 3. 运行

建议在服务器或这台长期开机的电脑上运行，使用 `nohup` 或 `screen` 挂后台：

**Bash**

```
python fetch.py
```

或者：

**Bash**

```
nohup python3 fetch.py > grade.log 2>&1 &
```

## ⚙️ 高级配置

### 修改轮询间隔

在 `main()` 函数中，你可以调整 `time.sleep` 的参数来改变轮询频率：

**Python**

```
# 默认在 10秒 到 2分钟 之间随机等待
time.sleep(random.uniform(10, 120))
```

### 修改推送格式

你可以修改 `format_desp` 函数来自定义推送到手机上的消息内容格式。

默认格式：

> 标题：新成绩：xxx
> 内容：科目：xxx 学分：x 成绩：xx

## ⚠️ 免责声明

* 本脚本仅供学习交流使用。
* 请勿将轮询间隔设置得过短，以免对学校服务器造成压力或导致账号被封禁。
* 开发者不对因使用本脚本导致的任何账号安全问题或数据泄露负责。

## 📄 License

MIT License
