# astrbot_plugin_nai_plus

通过 [Nai2API](https://github.com/STA1N156/Nai2API) 网关调用 NovelAI 生成图片的 AstrBot 插件。

## 简介

本项目基于 `helloWKQ/AstrBot_Nai2API` 重新起步，提供轻量、稳定、纯净的 NovelAI 接入支持。从版本 `v0.0.1` 开始全新迭代。

## 功能特性

- **独立 WebUI 预设管理**：在 AstrBot 插件页面内集成原生的「NovelAI 预设管理」面板，可视化查看、编辑、新建、删除预设，并一键设为默认预设。
- **三段式预设架构**：每个预设支持独立配置「质量词/画师串」、「附带正向词」与「附带负向词」：
  - **质量词/画师串**：作为独立 `artist` 参数发送至服务端，保持画风隔离；画师串中的负权重标签自动分流到负向词。
  - **附带正向词**：套用预设时自动追加至用户提示词后。
  - **附带负向词**：套用预设时自动与全局负向词智能去重合并。
- **默认预设支持**：支持设置默认全局预设（生图免写 `-p`），并支持 `--no-preset` 单次临时禁用。
- **实时模拟预览**：WebUI 面板提供测试提示词与实时预览，所见即所得查看最终组装的 artist / tag / negative。
- **文生图指令**：`/nai` 指令快速生成图片，支持指定尺寸、预设、画师前缀、负面提示词与随机种子。
- **LLM 生图工具**：内置标准函数调用工具，AI 对话助手可直接调用生图。
- **状态与余额**：提供 `/nai balance` 快速查询点数余额及账号状态。
- **信息标记**：生成图片附带预设与耗时信息，便于观察出图效果。

## 安装方式

将本仓库克隆或下载解压至 AstrBot 的插件目录：

```bash
cd data/plugins/
git clone https://github.com/Shawlei/astrbot_plugin_nai_plus.git
```

重启 AstrBot 即可自动加载。

## 基础配置

在 AstrBot 管理面板 → 插件配置中填写：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `api_url` | Nai2API 服务端地址 | `https://nai.sta1n.cn` |
| `token` | Nai2API 用户密钥（必填） | 空 |
| `default_model` | 默认生图模型 | `nai-diffusion-4-5-full` |
| `default_size` | 默认图片尺寸 | `竖图` |
| `default_steps` | 默认步数（1-28） | `28` |
| `default_scale` | 提示词引导系数 (CFG) | `6` |
| `default_sampler` | 采样器 | `k_dpmpp_2m_sde` |
| `default_artist` | 默认画师前缀 | 官方默认 |
| `default_negative` | 默认负向提示词 | 官方默认 |
| `default_preset` | 默认画风预设名称（可在 WebUI 面板一键设置） | 空 |
| `custom_presets` | 自定义预设列表（与 WebUI 面板双向同步） | `[]` |
| `timeout` | 请求超时时间（秒） | `120` |
| `show_image_info` | 是否在图片下方显示耗时与预设 | `true` |
| `llm_tool_enabled` | 是否允许 AI 助手调用生图 | `true` |

## 快速使用

### 1. 指令生图

```text
/nai 1girl, white hair, blue eyes
/nai 竖图 1girl, smiling
/nai 横图 -p 动漫风 1girl, city street
/nai 2K竖图 1girl, masterpiece --seed 123456
/nai 1girl, solo --no-preset              # 临时跳过已设置的默认预设
```

### 2. 预设管理（WebUI 与指令）

- **WebUI 可视化面板**：
  进入 AstrBot 管理面板 → 导航栏「插件 Pages」→ 点击「NovelAI 预设管理」。
  可直观编辑每个预设的质量词/画师串、附带正向词、附带负向词，一键点击「设为默认」，并进行实时组装预览。

- **聊天指令操作**：
```text
/nai presets                    # 列出所有可用预设
/nai presets <预设名>            # 查看指定预设详情
/nai default                    # 查看当前默认预设
/nai default <预设名>            # 将指定预设设为默认（免写 -p）
/nai default clear              # 取消默认预设
/nai save <预设名> <画师串>      # 新建或覆盖自定义预设
/nai del <预设名>               # 删除自定义预设
/nai update <预设名> <画师串>    # 修改自定义预设
```

### 3. 余额查询

```text
/nai balance                    # 查询当前点数与各分辨率预计可生成张数
```

## 目录结构

```text
astrbot_plugin_nai_plus/
├── main.py                     # 插件入口，注册指令、LLM 工具与 WebAPI
├── metadata.yaml               # 插件元信息（版本 0.1.0）
├── _conf_schema.json           # 插件配置项 Schema 定义（含 template_list）
├── requirements.txt            # Python 依赖清单
├── README.md                   # 仓库说明文档
├── CHANGELOG.md                # 版本更新日志
├── AI_MODIFY_RULES.md          # 规范与修改准则
├── .astrbot-plugin/            # AstrBot 插件 Pages 国际化声明
│   └── i18n/
│       ├── zh-CN.json
│       └── en-US.json
├── pages/                      # 独立 WebUI 页面静态资源
│   └── nai-config/
│       ├── index.html          # 面板 HTML 结构
│       ├── style.css           # 面板主题样式
│       └── app.js              # 前端交互与桥接逻辑
├── core/
│   ├── nai2api_client.py       # API 请求客户端
│   ├── image_manager.py        # 图片下载与缓存管理
│   └── preset_manager.py       # 预设配置与三段式组装
└── tests/                      # 自动化回归测试套件
    └── test_webui_and_presets.py
```
