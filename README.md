# astrbot_plugin_nai_plus

通过 [Nai2API](https://github.com/STA1N156/Nai2API) 网关调用 NovelAI 生成图片的 AstrBot 插件。

## 简介

本项目基于 `helloWKQ/AstrBot_Nai2API` 重新起步，提供轻量、稳定、纯净的 NovelAI 接入支持。从版本 `v0.0.1` 开始全新迭代。

## 功能特性

- **文生图指令**：`/nai` 指令快速生成图片，支持指定尺寸、预设、画师前缀、负面提示词与随机种子。
- **预设管理**：内置官方 5 大常用画风预设，支持自定义预设的添加、查看、修改与删除。
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
```

### 2. 预设管理

```text
/nai presets                    # 列出所有可用预设
/nai presets <预设名>            # 查看指定预设详情
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
├── main.py                     # 插件入口，注册指令与事件处理
├── metadata.yaml               # 插件元信息（版本 0.0.1）
├── _conf_schema.json           # 插件配置项 Schema 定义
├── requirements.txt            # Python 依赖清单
├── README.md                   # 仓库说明文档
├── CHANGELOG.md                # 版本更新日志
├── AI_MODIFY_RULES.md          # 规范与修改准则
├── core/
│   ├── nai2api_client.py       # API 请求客户端
│   ├── image_manager.py        # 图片下载与缓存管理
│   └── preset_manager.py       # 预设配置管理
├── data/
│   ├── cmd_config.json         # 指令别名与触发规则
│   └── t2i_templates/          # 模板资源
└── persona/
    └── nai_artist_persona.md   # 生图助手人格提示词模版
```
