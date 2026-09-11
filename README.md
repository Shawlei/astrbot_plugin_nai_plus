# astrbot_plugin_nai_plus

通过 [Nai2API](https://github.com/STA1N156/Nai2API) 网关调用 NovelAI 生成图片的 AstrBot 插件。

> **这是增强版（nai_plus）**，基于 [helloWKQ/AstrBot_Nai2API](https://github.com/helloWKQ/AstrBot_Nai2API) 二开。
> 插件名特意用了 `astrbot_plugin_nai_plus`，所以**可以和原版同时安装**，互不冲突。

## 致谢

本项目的绝大部分基础功能来自 **[helloWKQ](https://github.com/helloWKQ)** 的原版插件，感谢原作者的开源：

- 原项目：[helloWKQ/AstrBot_Nai2API](https://github.com/helloWKQ/AstrBot_Nai2API)
- 上游依赖：[STA1N156/Nai2API](https://github.com/STA1N156/Nai2API) 网关
- 图片能力：[NovelAI](https://novelai.net/)

本增强版在其基础上新增了：

| 增强内容 | 版本 |
|---------|------|
| NovelAI V5 模型支持、按「模型+尺寸」区分扣点、V5 普通尺寸二次确认 | v1.2.0 |
| 提示词直译（中文/英文 → 英文标签）、翻译模型轮询、`-m` 切模型、自定义命令名 | v1.3.0 |

具体改动见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- `/nai` 指令文生图，支持尺寸、预设、模型、质量前缀、负面提示词、随机种子
- LLM Tool 调用生图（AI 助手可直接调用）
- 支持 NovelAI V5 最新模型（含 V5 Full / V5 Curated）
- **提示词直译**：中文/英文都会自动翻成 NovelAI 英文标签，不用自己写标签
- **内置 1572 条词库**：角色名 / 外观 / 场景直接查表，不走模型 —— 角色名 100% 准确，纯标签输入生图更快
- **翻译结果校验**：翻完检查有没有残留中文，有就自动重翻，防止模型「翻一半交卷」
- **翻译模型轮询**：配多个翻译模型，前一个失败自动换下一个
- **OpenAI 兼容接口配置友好**：地址/密钥分开填，模型可从接口实时拉取下拉选择
- **自定义命令名**：`/nai` 可以改成 `/niu`、`/绘` 等，支持多个别名
- **图生图**：回复一张图再发指令即可。后端是**通用 HTTP 模板**，任何支持图生图的渠道都能接
- 5 个内置预设（来自 Nai2API 官方前端）+ 自定义预设保存/修改/删除
- **群聊黑名单**：指定群彻底禁生图，且机器人完全静默不回复（连 AI 助手也收不到消息）
- 支持普通/2K/4K 分辨率
- 高扣点二次确认（V5 普通图 5 点、2K 15 点、4K 25 点），防误扣
- 图片本地缓存，自动清理
- 配套人格提示词（生图助手），无需手动写英文标签

> **关于图生图的后端选择**：NovelAI 本身是支持图生图的（`reference_image_multiple`），
> 但 Nai2API 网关的 `/generate` 接口没把它暴露出来 —— 请求体里的 `action` 被硬编码成
> `generate`，参考图字段也始终是空数组。所以：
>
> - **文生图**走 Nai2API（本插件的主流程）
> - **图生图**走你自己配置的渠道，通过「图生图」配置里的**通用 HTTP 模板**对接
>
> 只要你的渠道有图生图接口（OpenAI `images/edits`、Stable Diffusion WebUI 的
> `/sdapi/v1/img2img`、各种中转站的图生图端点…），照着下面的教程填一下就能用。

## 前置要求

- 运行中的 [Nai2API](https://github.com/STA1N156/Nai2API) 服务
- Nai2API 用户密钥（`STA1N-xxx` 格式，在 Nai2API 后台生成）

## 安装

将 `astrbot_plugin_nai_plus` 目录放入 AstrBot 的 `data/plugins/` 下，重启 AstrBot。

## 配置

在 AstrBot 管理面板 → 插件配置中填写：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `api_url` | Nai2API 服务地址 | `https://nai.sta1n.cn` |
| `token` | 用户密钥（必填） | 空 |
| `default_model` | 默认模型（V5 普通图 5 点，V4.5 普通图 1 点） | `nai-diffusion-5-full` |
| `default_size` | 默认尺寸 | `竖图` |
| `default_steps` | 默认步数 | `28` |
| `default_scale` | 默认提示词引导值 | `6` |
| `default_cfg` | 默认 CFG rescale（0-1） | `0` |
| `default_sampler` | 默认采样器 | `k_dpmpp_2m_sde` |
| `default_negative` | 默认负面提示词 | Nai2API 官方默认 |
| `default_artist` | 默认画师串/质量前缀 | 2.5D唯美风（Nai2API 官方默认） |
| `auto_composition` | 自动补构图标签（防止张张都是半身） | `true` |
| `default_noise_schedule` | 默认噪声调度 | `karras` |
| `timeout` | 请求超时(秒) | `120` |
| `llm_tool_enabled` | 允许 LLM 调用生图（AI 助手需要） | `true` |
| `group_blacklist` | 群聊黑名单（填群号，逗号或换行分隔；黑名单群彻底静默、绝不生图） | 空（不启用） |
| `show_image_info` | 图片信息标签（在图片下方显示预设名和耗时） | `true` |
| `confirm_hd_size` | 高扣点生图二次确认（V5 普通图、2K、4K） | `true` |
| `allow_2k` | 允许生成 2K 图片（关闭后自动降级为普通尺寸，防误扣 15 点） | `true` |
| `allow_4k` | 允许生成 4K 图片（关闭后自动降级为普通尺寸，防误扣 25 点） | `true` |
| `max_cached_images` | 图片最大缓存数 | `50` |
| `command_names` | 命令名，多个用逗号分隔，如 `nai,niu,绘` | `nai` |
| `translate_enabled` | 开启提示词直译（中文/英文 → 英文标签） | `true` |
| `translate_mode` | 直译接入方式（下拉选择）：`用 AstrBot 的模型` / `自定义 OpenAI 兼容接口` | 用 AstrBot 的模型 |
| `translate_provider_ids` | AstrBot 模式的翻译模型，**直接从下拉框勾选**（可多选做轮询）；**留空自动使用 AstrBot 所有可用模型** | 空 |
| `translate_openai_prefill` | OpenAI 接口预设（下拉选择），选中后自动填好地址和常用模型 | 自定义 |
| `translate_openai_base_url` | OpenAI 接口地址，填到 `/v1` 为止 | 空 |
| `translate_openai_api_key` | OpenAI 接口密钥（`sk-xxx`，本地接口可留空） | 空 |
| `translate_openai_model` | 直译模型，**可从接口实时拉取列表下拉选择**，也可手填 | 空 |
| `translate_dictionary_enabled` | **词库直译开关**（命中词库直接查表，不调模型） | `true` |
| `translate_dictionary_match` | 词库匹配模式：`segment`=仅精确匹配（推荐）/ `substring`=允许内嵌替换 | `segment` |
| `translate_verify_enabled` | 翻译结果校验（检查残留中文并重翻） | `true` |
| `translate_verify_retry` | 检测到残留中文时重翻次数 | `1` |
| `translate_verify_on_fail` | 重翻后仍有中文：`warn`=提醒但继续 / `abort`=终止不生图 | `warn` |
| `translate_user_dict_path` | **自定义词库文件路径**（.json / .txt，见「词库」章节） | 空 |
| `translate_system_prompt` | 直译系统提示词，**已预填内置提示词 + few-shot 示例**，可直接改；清空则恢复内置默认 | 内置提示词 |
| `translate_timeout` | 单次翻译超时(秒) | `60` |
| `translate_on_error` | 翻译失败时：`fallback`=用原文继续 / `abort`=终止不生图 | `fallback` |
| `img2img_enabled` | 开启图生图板块（关闭后「图生图」配置块整体隐藏） | `false` |
| `img2img` | 图生图渠道配置（见下方教程） | 见教程 |

> **重要**：如果要让 AI 助手自动调用生图，请确保 `llm_tool_enabled` 为 `true`，
> 并启用 AstrBot 人格中引用的生图助手人格提示词。

---

## 图生图配置教程

### 1. 它是怎么工作的

插件本身不懂任何渠道的图生图协议。它做的事只有三件：

1. 从你「回复的那条消息」里取出一张图
2. 把这张图和你写的提示词，按你填的**模板**拼成一个 HTTP 请求
3. 把渠道返回的图片抠出来发给用户

所以「模板」是整个图生图能力的核心 —— 渠道文档里的请求体长什么样，
你就把它粘进来，把具体的值换成 `{{占位符}}`。

### 2. 可用占位符

| 占位符 | 含义 |
|--------|------|
| `{{prompt}}` | 提示词（已经过直译的英文标签） |
| `{{negative}}` | 负面提示词 |
| `{{image_base64}}` | 参考图 base64（**不含** `data:` 前缀） |
| `{{image_data_url}}` | 参考图 data URL（**含** `data:image/png;base64,` 前缀） |
| `{{strength}}` | 相似度 0~1 |
| `{{noise}}` | 降噪 0~1 |
| `{{seed}}` | 随机种子 |
| `{{model}}` | 模型名 |
| `{{size}}` | 尺寸 |

两个细节：

- **未知占位符会原样保留**，不会被静默清空。模板写错名字时你能一眼看出来（日志里也有告警）。
- 值的引号由你控制。写 `"prompt": "{{prompt}}"` 里的引号是模板自带的，
  插件不会多加也不会少加 —— 所以 JSON 模板里**记得自己带上引号**。

### 3. 两种提交格式

**`multipart/form-data`** —— 图片以文件形式上传，多数渠道用这个
（OpenAI `images/edits`、Stable Diffusion WebUI…）。

`body_template` 每行写一个 `key=value`：

```
prompt={{prompt}}
negative_prompt={{negative}}
denoising_strength={{strength}}
```

> 参考图是通过「图片字段名」单独带上去的（默认 `image`，SD WebUI 要改成 `init_images`），
> **不用**写进模板里。

**`application/json`** —— 图片以 base64 塞进 JSON（NovelAI、部分中转站）。

`body_template` 直接写 JSON：

```json
{
  "prompt": "{{prompt}}",
  "negative_prompt": "{{negative}}",
  "image": "{{image_base64}}",
  "strength": {{strength}},
  "noise": {{noise}}
}
```

> 注意 `{{strength}}` 两侧**不加引号**，因为它是数字，加引号可能被渠道拒绝。

### 4. 各渠道填法示例

<details>
<summary><b>OpenAI images/edits（含官方及各家兼容中转）</b></summary>

| 配置项 | 值 |
|--------|-----|
| 接口地址 | `https://api.openai.com/v1/images/edits` |
| 请求方式 | `POST` |
| 接口密钥 | `sk-xxx` |
| 鉴权头名称 | `Authorization` |
| 鉴权头前缀 | `Bearer ` |
| 提交格式 | `multipart/form-data` |
| 图片字段名 | `image` |
| 响应图片路径 | `data.0.b64_json` |
| 请求体模板 | `model={{model}}`<br>`prompt={{prompt}}`<br>`n=1`<br>`size={{size}}` |

</details>

<details>
<summary><b>Stable Diffusion WebUI（/sdapi/v1/img2img）</b></summary>

| 配置项 | 值 |
|--------|-----|
| 接口地址 | `http://127.0.0.1:7860/sdapi/v1/img2img` |
| 提交格式 | `application/json` |
| 图片字段名 | *(multipart 才用，这里留默认)* |
| 响应图片路径 | `images.0` |
| 请求体模板 | 见下方 JSON |

```json
{
  "prompt": "{{prompt}}",
  "negative_prompt": "{{negative}}",
  "init_images": ["{{image_base64}}"],
  "denoising_strength": {{strength}},
  "sampler_name": "DPM++ 2M Karras",
  "steps": 28,
  "cfg_scale": 6
}
```

</details>

<details>
<summary><b>通用「图生图」中转站（base64 + JSON）</b></summary>

多数中转站都提供 OpenAI 风格的 `/v1/images/edits`，直接套用第一个示例即可。
如果接口路径不同，只改「接口地址」这一项，模板保持不动。

如果返回的是 URL 而不是 base64，把「响应图片路径」改成 `data.0.url`
（插件会自动下载该 URL）。不确定路径时**留空**也行 —— 插件会扫一遍常见字段名。

</details>

### 5. 在聊天里用

**最基本**：发一张图 → 长按/右键**回复**那张图 → 发送 `/nai 你的描述`

```
（回复一张人物照片）
/nai 1girl, 银发, 站在窗边, 逆光
```

**临时调参**：

| 参数 | 作用 |
|------|------|
| `--strength 0.6` | 相似度，越大越贴近原图 |
| `--noise 0.15` | 降噪，越大离原图越远 |
| `--i2i` | 强制走图生图（没找到参考图会明确报错） |
| `--no-i2i` | 强制走文生图（即使回复里有图） |

```
/nai 改成赛博朋克风格 --strength 0.55 --noise 0.35
```

**自动判定规则**：图生图总开关打开 + 回复的消息里有图 → 自动走图生图；
没有图就走原来的文生图。想强制指定就用上面两个参数。

### 6. 常见问题

| 现象 | 原因与解决 |
|------|-----------|
| **画风不生效** | v1.2.2 已修。此前画师串被当成独立请求参数发送、没拼进提示词，导致预设里的画风与构图标签全部失效。升级到 v1.2.2+ 即可 |
| **张张都是上半身** | v1.2.5 已修。NovelAI 在没有任何构图/景别标签时，会退回训练数据的统计偏好 —— 默认出 `portrait` / `upper body`。而画师串只管风格质感，用户 prompt 常只写「谁 + 穿什么」，「拍到哪」是空的。现在内置预设都带了构图标签，且当提示词和画师串都没给构图约束时会自动补 `full body, standing`。想固定画头像就关掉 `auto_composition`，或在提示词里写「半身 / 胸像 / 特写」 |
| `检测到参考图，但图生图渠道还没配置好` | 「接口地址」和「请求体模板」是必填项，缺一不可 |
| `你加了 --i2i 要求图生图，但没找到参考图` | 记得**回复**带图的消息再发指令，直接发指令是找不到图的 |
| 图生图不走二次确认 | 这是有意设计。图生图走的是你的渠道，扣点规则和 Nai2API 无关，插件无法预估 |
| 报错「响应既不是图片也不是 JSON」 | 渠道返回了错误信息。检查密钥、图片字段名、模板格式是否和渠道文档一致 |
| 返回的图路径找不到 | 先留空让插件自动识别；自动识别也失败时，按渠道文档手填，如 `data.0.b64_json` |
| **黑名单群完全不生图**（预期行为） | v1.3.0 新增。在 `group_blacklist` 里填了群号，该群生图功能会彻底静默 —— 这是配置生效了，不是坏了。清空该项即可恢复 |
| 黑名单填了但没生效 | 群号必须是**纯数字**（垃圾项会被静默跳过）；改配置后需要重载插件；用日志里的 `[Nai黑名单] 已启用，共 N 个群被拉黑` 确认是否加载成功 |
| 黑名单群连 AI 聊天都不回了 | v1.3.0 的设计如此。第 3 层拦截会掐断整个 LLM 请求，「让 LLM 无视生图」的代价就是它收不到消息。如果只想禁生图、保留聊天，看重名单章节的「取舍」 |
| 私聊还能生图？ | 正常。`group_blacklist` 是**群**黑名单，只管群聊，私聊不受影响 |

---

## 词库（提示词直译加速）

### 1. 为什么需要词库

翻译模型有两个绕不开的毛病：

- **专有名词靠猜**。「雷电将军」被翻成 `raiden shogun`（对）还是 `thunder general`（错）全看运气。但这事其实是**确定性的** —— 官方 Danbooru 标签就那一个。
- **每次都要等网络**。哪怕只写了「白发 双马尾 微笑」这种纯标签，也得等模型转一圈才拿到 `white hair, twintails, smile`。

词库解决的就是这两件事：**命中词库的词直接查表替换，不调用模型**。

```
用户输入 → 查词库 → 命中的直接替换
                 → 没命中的才交给翻译模型 → 校验有没有残留中文
```

实测效果（22 条真实提示词，1572 条内置词库）：

| 输入类型 | 词条命中率 | 是否调用模型 |
|---------|-----------|------------|
| 纯属性罗列（`白发, 双马尾, 微笑`） | **100%** | ❌ 完全不调 |
| 角色 + 属性（`雷电将军, 紫发, 和服`） | **100%** | ❌ 完全不调 |
| 带场景短语（`白发少女, 站在樱花树下`） | ~50% | ✅ 只翻没命中的部分 |
| 完全口语化（`一个女孩坐在窗边…`） | ~0% | ✅ 交给模型 |

综合看：**77% 的提示词可以完全绕过翻译模型，词条级命中率 88%**。

也就是说，**写标签式提示词时生图速度会明显变快，角色名 100% 不会错**。

几个实际会被兜住的输入写法（都来自真实群聊）：

| 用户实际怎么打 | 处理 |
|--------------|------|
| `鸣潮达妮娅`（不打空格） | 连写兜底，切出 `鸣潮` + `达妮娅`，不会整体丢给模型 |
| `画一个泳装爱蜜莉雅` | 自动剥掉口语前缀「画一个」，再查表 |
| `达妮娅 裸体围裙` | 命中 `naked apron` 整体 tag，不会拆成半对半错的 `裸体, apron` |
| `超级赛亚人发型`（词库没有） | **宁可整体交模型，也不做半截替换** |

在聊天里随时可以看词库状态和测试命中：

```
/nai dict                      查看词库概况
/nai dict 白发 双马尾 微笑        测试这段文本的命中情况
```

### 2. 内置词库

| 文件 | 内容 | 条数 |
|------|------|------|
| `core/prompt_dict/characters.json` | 角色名：原神 / 崩铁 / 绝区零 / 蔚蓝档案 / 明日方舟 / FGO / 东方 Project / 经典动画 / VTuber / 战双 / 鸣潮 / 单机游戏 | **823** |
| `core/prompt_dict/appearance.json` | 发色发型 / 眼睛 / 表情 / 体型 / 服装 / 姿势 / 镜头 | **469** |
| `core/prompt_dict/scene.json` | 环境背景 / 画风 / 动作 / 构图 | **280** |
| 合计 | 3 个文件 | **1572** |

合计 **1572 条**（去重后加载 **1572 条**）。角色名全部对照 Danbooru 官方标签，不做音译。

`characters.json` 覆盖的作品（19 个分组）：

| 分组 | 条数 | 分组 | 条数 |
|------|------|------|------|
| 原神 | 97 | 东方 Project | 94 |
| 经典动画 | 198 | 崩坏：星穹铁道 | 63 |
| hololive | 57 | 绝区零 | 38 |
| 蔚蓝档案 | 32 | 其他 VTuber（含 Vocaloid） | 32 |
| 明日方舟 | 25 | Fate / Grand Order | 22 |
| 彩虹社 | 16 | 单机游戏（FF / 尼尔 / 生化 / OW 等） | 15 |
| 碧蓝航线 | 13 | 赛马娘 | 12 |
| 崩坏 3 | 10 | 公主连结 | 10 |
| 少女前线 | 7 | Fate / TYPE-MOON | 4 |
| 鸣潮 | 33 | 战双帕弥什 | 19 |

### 3. 防止模型「脑补」（重要）

词库只能覆盖已知角色。**遇到词库里没有的角色名时，模型会自己猜**——这是
「明明翻译看着没问题，画出来却不对」的头号原因。

举个真实案例：

```
输入: 战双 比安卡 泳装        ← 用户只说了 3 个词
模型输出: 1girl, bianca_(punishing:_gray_raven), swimsuit, bikini,
          ponytail, blonde_hair, green_eyes, masterpiece, best_quality
          └─ 用户说的 ─┘   └──── 模型自己猜的，而且猜错了 ────┘
```

「比安卡」不在词库里 → 整句丢给模型 → 模型不知道她长什么样，就编了一套
（马尾/金发/绿眼），还把「泳装」拆成 `swimsuit` + `bikini` 两个打架的标签。

针对这个，翻译提示词里加了三条硬约束：

| 规则 | 作用 |
|------|------|
| 11 | **禁止添加用户未提及的外观**；不认识的角色只输出角色名，不许猜长什么样 |
| 12 | **禁止输出近义标签**（`swimsuit`+`bikini` → 只留 `swimsuit`） |
| 13 | **不确定就不加**。少而准远好过多而猜 |

代码层还有一道兜底：`_collapse_synonyms()` 会在结果返回前把近义标签折叠掉，
即使模型没听话也不会让 `swimsuit` 和 `bikini` 同时生效。

> 为什么近义标签危害大：在 NovelAI 里**重复概念等于加权**。`swimsuit` 和
> `bikini` 是**不同款式**，同时写会让模型在两者之间摇摆，衣服经常画得
> 不伦不类；`1girl` + `female` 同理。

**根治办法还是补词库。** 遇到常画但不认识的角色，用下面的自定义词库加进去。

### 4. 添加自己的词条（重点）

**内置词库会被插件更新覆盖，所以不要直接改它。** 请用「自定义词库文件」：

1. 新建一个文件，比如 `/AstrBot/data/my_nai_dict.json`
2. 在插件配置的 **「自定义词库文件路径」** 里填入这个路径
3. 重启插件生效

> 路径可以写绝对路径，也可以写相对路径（相对路径按 AstrBot 的数据目录算）。
> 留空则只用内置词库。

**JSON 格式**（推荐，带分类便于维护）：

```json
{
  "群里的角色": {
    "我的OC": "my_original_character",
    "团长": "my_group_leader"
  },
  "常用短语": {
    "站在樱花树下": "standing under cherry blossoms",
    "雨中的街道": "rainy street"
  }
}
```

**TXT 格式**（适合随手追加）：

```
# 井号开头是注释，会被忽略
我的OC=my_original_character
团长=my_group_leader
雨中的街道=rainy street

// 双斜杠也是注释
另一个词,可以逗号分隔
```

> 每行 `中文=英文` 或 `中文,英文`，取第一个分隔符。

**你的词条优先级高于内置** —— 如果觉得内置某条翻得不对，直接在自定义词库里写同名条目覆盖它，不用改源码。

### 5. 匹配模式怎么选

| 模式 | 行为 | 适用 |
|------|------|------|
| **仅精确匹配**（默认） | 按逗号/空格切成词，逐词查表 | 不会误伤，推荐 |
| 允许内嵌替换 | 在整句里做子串替换 | 能命中「白发少女站在樱花树下」这类连写句，但短词有误替换风险 |

两种模式下，NovelAI 权重语法（`1.3::tag::`、`{{tag}}`、`[tag]`、`artist:name`）都会被**整体保护**，不会被切碎或替换。

### 6. 翻译结果校验

模型「翻一半就交卷」是很常见的行为 —— 尤其遇到不认识的中文时，它会直接把原文抄回来。这种结果 NovelAI 完全读不懂，但界面上看起来「翻过了」。

所以翻完之后会再检查一遍**有没有残留中文**：

1. 有残留 → 自动重翻（重翻时会明确告诉模型「你上次没翻干净」）
2. 重翻后仍有 → 按你的设置：**提醒但继续生图**（默认）或 **终止不生图**（省点数）

重翻次数和失败策略都在配置里可调。

### 7. 相关配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `translate_dictionary_enabled` | 词库直译开关 | `true` |
| `translate_dictionary_match` | 匹配模式：`segment` 精确 / `substring` 内嵌 | `segment` |
| `translate_verify_enabled` | 翻译结果校验开关 | `true` |
| `translate_verify_retry` | 检测到残留中文时重翻次数 | `1` |
| `translate_verify_on_fail` | 重翻后仍有中文：`warn` 继续 / `abort` 终止 | `warn` |
| `translate_quality_weight` | 质量词自动加权开关 | `true` |
| `translate_quality_weight_value` | 质量词权重倍数（只填数字） | `1.2` |
| `translate_user_dict_path` | 自定义词库文件路径 | 空 |

#### 质量词自动加权

开启后，`best quality` / `masterpiece` / `absurdres` / `very aesthetic` /
`detailed` 这类质量标签会被自动包上权重，变成 `1.2::best quality::`。

**为什么只给质量词加，不给内容词加**：给 `swimsuit` 这类内容标签加权重会让
这个概念过拟合，挤掉画面其他部分（这正是「画不对」的常见原因）；而质量词加
权重是纯收益——它提升整体画面精度，不和具体内容争抢表现力。

**已有的权重不会被覆盖**：`1.3::masterpiece::`、`{{masterpiece}}`、
`[best quality]` 一律原样保留，不会出现 `1.2::1.3::xxx::::` 这种嵌套。

### 8. 常见问题

| 现象 | 原因与解决 |
|------|-----------|
| 日志里没有「词库全命中」 | 检查 `translate_dictionary_enabled` 是否开启，或这段文本确实没命中 |
| 加了自定义词库但不生效 | 重启插件；用 `/nai dict <文本>` 验证是否加载成功；检查路径 |
| 内置词条翻得不满意 | 在自定义词库里写同名条目覆盖它 |
| 想让所有输入都走词库 | 把 `translate_dictionary_match` 改成「允许内嵌替换」 |
| 频繁提示「仍有中文标签」 | 把这些词加进自定义词库，或换更强的直译模型 |

---

## 群聊黑名单

想让某些群的生图功能**彻底哑火**，又不想让群里的人看出机器人被禁了？

配置里找到 `group_blacklist`，填群号，多个用逗号或换行分隔：

```
123456789
987654321
```

留空 = 不启用，所有群照常。

### 行为

黑名单里的群：

- ❌ 无论谁发 **`/nai` 指令**，都不生图
- ❌ 无论谁 **@ 机器人**、或者让它「画一个 xxx」，都不生图
- ❌ AI 助手**根本不会收到这条消息**，也就无从"决定要不要画"
- 🤫 机器人**不会在该群发任何回复** —— 连「功能已禁用」都不会说

群里看到的和机器人不存在时完全一样。

> **只对群聊生效**，私聊不受影响。这是「群黑名单」，不是「用户黑名单」——
> 群被拉黑后，那个群里的人私聊机器人照样能生图。

### 支持的填写格式

配置解析很宽容，下面这些写法都认：

| 写法 | 示例 |
|---|---|
| 逗号（中英文） | `123, 456` |
| 换行 | 一行一个群号 |
| 分号 / 顿号 / 竖线 / 空格 | `123；456`、`123、456`、`123\|456` |
| 带前缀 | `群号:123`、`QQ群:123`、`gid:123`（前缀会被自动剥掉） |
| 多平台 | `aiocqhttp:123, telegram:456`（只取群号部分） |

解析不出来的项会被**静默跳过**，不会让插件加载失败。配置写错最多是黑名单不生效，
但插件挂掉的话你连生图都用不了了 —— 所以这里一律容错。

### 实现：三层拦截

为了确保**没有漏网的可能**，同时在三个地方拦截：

| 层 | 拦截点 | 作用 |
|---|---|---|
| 第 1 层 | `#nai` 命令入口 | 整个命令链路静默丢弃（`presets`、`balance` 等子命令一视同仁） |
| 第 2 层 | 6 个 `nai_*` LLM 工具入口 | 给模型回一句「本群未启用」，让它收手、不再重试 |
| 第 3 层 | `on_llm_request` 钩子 | **直接掐断 LLM 请求**，模型根本收不到消息 |

前两层保证「**生不出图**」，第 3 层额外保证「**LLM 压根不知道有人要生图**」。

<details>
<summary>为什么第 3 层要用 <code>on_llm_request</code>，而不是把工具的 <code>active</code> 设成 <code>False</code>？</summary>

这是个坑，记一下。

AstrBot 的工具对象确实有 `active` 字段，`get_light_tool_set()` /
`get_param_only_tool_set()` 也会过滤它。**但主链路不走这两个方法。**

实际走的序列化方法是 `ToolSet.openai_schema()`，而它**没有 `active` 过滤**：

```python
def openai_schema(self, omit_empty_parameter_field: bool = False) -> list[dict]:
    result = []
    for tool in sorted(self.tools, key=lambda tool: tool.name):
        func_def = {"type": "function", "function": {"name": tool.name}}
        ...
        result.append(func_def)
    return result
```

调用方还用 `if tool_list:` 判断（非空列表恒为真）：

```python
tool_list = tools.get_func_desc_openai_style(...)
if tool_list:
    payloads["tools"] = tool_list      # ← active=False 的工具照样在里面
```

所以 `active=False` 只是让工具在面板上显示成禁用，**该发给模型还是发**。

另外 `llm_tools` 是**全局注册表**，不是按群隔离的，也没法「只给某个群摘掉工具」。

`on_llm_request` 则在请求发给模型**之前**触发，`event.stop_event()`
会让 pipeline 直接放弃这次调用（`internal.py` 里的
`if await call_event_hook(...OnLLMRequestEvent, req): return`）。
模型收不到消息 —— 这才是真正的「从源头掐断」。

</details>

### 取舍

第 3 层一停，**这个群的 LLM 请求就整个没有了**，不只是生图。

这是「让 LLM 无视生图指令」的必然结果：要它无视，就得让它收不到。
如果你希望黑名单群保留正常的 AI 聊天、只是不能生图，那就用前两层就够了。

好消息是这个钩子只作用于**本插件**，不会动 AstrBot 的全局会话配置，
也不影响该群里其它插件的正常工作。

---

## 使用方式

本插件提供**两种使用方式**，根据你的习惯选择：

### 方式一：AI 助手自动画图（推荐新手）

配置好人格提示词后，直接用中文跟 AI 说你想要什么，
AI 会自动把你的描述翻译成英文标签并调用生图。

```
用户：帮我画一个银发蓝眼的女孩，站在雨夜霓虹街头
AI → 自动调用 nai_generate 生成图片

用户：来一张 2K 高清壁纸，二次元风景，日落
AI → 自动调用 nai_generate 生成图片

用户：帮我画个 GalGame 风格的女生，穿校服
AI → 自动调用 nai_generate 生成图片
```

> 使用方式一需要配置"生图助手"人格提示词，详见下方【人格提示词】章节。

### 方式二：手动写 `/nai` 指令

直接在 `/nai` 后面写**中文描述**或英文标签都可以（会自动直译）：

```
/nai 一个银发蓝眼的女孩
/nai 1girl, silver hair, blue eyes
```

**提示词直译** — 中文/英文都会先翻成 NovelAI 能认的英文标签再生成：

```
/nai 一个穿着水手服的女孩，站在樱花树下   →  1girl, sailor uniform, cherry blossoms
/nai a girl with long silver hair          →  1girl, long hair, silver hair
```

> - 直译是**内置功能，默认开启**，装完就能用，不需要额外配 key
> - 默认直接使用 AstrBot 里已经配好的模型（`translate_mode = astrbot`）
> - 想用别的便宜模型专门翻译？把 `translate_mode` 改成 `openai` 并填接口即可
> - NovelAI 的权重语法（`1.2::tag::`、`{{tag}}`、`[tag]`）和画师标签会被原样保留
> - 想关掉直译：`translate_enabled = false`

**切换模型** — 用 `-m` 指定本次使用的模型（V5 普通图 5 点，V4.5 普通图 1 点）：

```
/nai -m 5 一个女孩            → 用 V5（nai-diffusion-5-full）
/nai -m 4.5 一个女孩          → 用 V4.5（省钱，普通图只扣 1 点）
/nai -m 5c 一个女孩           → 用 V5 Curated
/nai -m furry 1girl           → 兽人风格
```

支持的简写：`5`、`5c`、`4.5`、`4.5c`、`4`、`3`、`furry`、`2`、`safe`，
也可以直接写全名（`nai-diffusion-5-full`）。长参数 `--model` 等效。

**改命令名** — 不喜欢 `/nai` 可以改（配置项 `command_names`）：

```
command_names = nai,niu,绘
```

之后 `/nai`、`/niu`、`/绘` 三个指令都能用。改完需要**重载插件**生效。

> ⚠️ 「`/`」或「`+`」这个**前缀符号**是 AstrBot 的全局设置（`data/cmd_config.json` 里的
> `wake_prefix`），插件改不了。想要 `+nai`，得先把全局前缀设成 `+`，插件里仍填 `nai`。
> 命令名里不能有空格。

**指定图片尺寸** — 在提示词前面加尺寸关键词：

```
/nai 竖图 1girl, silver hair          → 832×1216（默认）
/nai 横图 1girl, silver hair          → 1216×832
/nai 方图 1girl, silver hair          → 1024×1024
/nai 2K竖图 1girl, silver hair        → 1088×1600（扣15点）
/nai 4K横图 1girl, silver hair        → 1984×1344（扣25点）
```

> 不写尺寸就是竖图。

**使用预设** — 预设就是一组提前配好的"质量前缀"：

```
/nai -p GalGame风 1girl, silver hair  → 自动加 ningen_mame 等画师前缀
/nai -p 动漫风 1girl, silver hair      → 自动加多画师混合前缀
/nai 2K竖图 -p 韩漫小清新风 1girl      → 尺寸和预设可以组合
```

> 不写 `-p` 就用默认的 2.5D唯美风质量前缀。

**自定义质量前缀** — 用 `--artist` 临时指定，不用保存预设：

```
/nai 1girl --artist best quality, absurdres
/nai 1girl --artist artist:ningen_mame,, very aesthetic
```

**指定负面提示词** — 用 `--negative` 添加不想要的内容：

```
/nai 1girl --negative bad anatomy, bad hands, text
```

> 不写 `--negative` 就用配置里的默认负面提示词。

**指定随机种子** — 用 `--seed` 固定随机种子，相同种子+相同参数可复现图片：

```
/nai 1girl --seed 12345
```

> 不写 `--seed` 则每次随机。

**组合使用** — 尺寸、预设、模型、负面提示词、种子可以随意组合：

```
/nai 2K竖图 -p GalGame风 -m 5 一个女孩 --negative low quality --seed 42
```

**图生图** — 回复一张图再发指令，就能基于那张图生成（需要先配置图生图渠道）：

```
（回复一张参考图）
/nai 1girl, 银发, 站在窗边 --strength 0.6 --noise 0.2
```

| 参数 | 作用 |
|------|------|
| `--strength 0.6` | 相似度 0~1，越大越贴近原图 |
| `--noise 0.15` | 降噪 0~1，越大离原图越远 |
| `--i2i` | 强制走图生图 |
| `--no-i2i` | 强制走文生图 |

详细配置教程见上方的「[图生图配置教程](#图生图配置教程)」。

**直译模型配置**

默认（`translate_mode = astrbot`）**不用配任何东西**：插件会**自动发现** AstrBot 里已配置的对话模型，
挨个试，谁先成功用谁（会自动跳过 TTS / STT / 向量模型这类不能翻译的）。

想指定用哪个模型？在配置面板里点「翻译模型（AstrBot 模式）」右边的按钮，
**直接从下拉列表里勾选** AstrBot 已经配好的模型即可，不用手抄 ID：

- 勾一个：只用它翻译
- 勾多个：按顺序**轮询**，前一个失败（超时/限流/欠费/返回空）自动换下一个
- 一个都不勾：回到上面的「自动发现全部可用模型」

如果想彻底绕开 AstrBot、单独用一个 OpenAI 兼容接口来翻译，在配置面板上填**三个独立的框**即可
（不再是一个多行文本框）：

| 配置项 | 说明 |
| --- | --- |
| 「OpenAI 接口」 | 下拉选预设（OpenAI 官方 / 阿里云百炼 / DeepSeek / 硅基流动 / Kimi / 智谱 / OpenRouter / 本地 Ollama），选中后**自动帮你填好地址和常用模型** |
| 「OpenAI 接口地址」 | 填到 `/v1` 为止，例如 `https://api.openai.com/v1`。只填域名也能认，插件会自动补 `/v1` |
| 「OpenAI 接口密钥」 | `sk-xxxx`；本地 Ollama 这类不需要鉴权的可以留空 |
| 「直译模型」 | 点右边「获取模型列表」，插件会实时请求 `{地址}/models` **把可用模型拉成下拉框**；拉不到时也可以直接手填模型名 |

```
translate_mode = openai
translate_openai_prefill   = deepseek          # 选预设，自动填下面两项
translate_openai_base_url = https://api.deepseek.com/v1
translate_openai_api_key  = sk-xxxx
translate_openai_model    = deepseek-chat
```

> - 插件会自动拼 `/chat/completions` 和 `/models`，所以地址**不要**带这两段
> - 拉取模型列表失败（网络不通 / 没填 key / 接口不支持 `/models`）只会打日志，
>   配置面板照常渲染，「直译模型」退化成普通输入框，手填一样能用
> - 翻译失败可以配置 `translate_on_error` 决定是回退用原文生图还是直接中止

**直译系统提示词**

配置里的「直译系统提示词」**已经预填了插件内置的提示词**，不用自己写。
内置版本主要做了这些约束：

- 只输出英文标签，不要解释、不要 markdown、不要引号
- 保留 NovelAI 权重语法：`1.2::tag::`、`{{tag}}`、`[tag]`、`-2::tag::`、`\n20::tag::`
- 保留 `artist:name` 这类画师标签
- 不输出中文、尽量保持标签顺序

想微调效果（比如强制某种画风、加固定质量词）可以直接在框里改。
改坏了想恢复，把内容清空保存即可，插件会自动用回内置提示词。
> - 翻译失败时怎么办由 `translate_on_error` 决定：
>   - `fallback`（默认）：用原文继续生图，并提示一句
>   - `abort`：直接终止，避免翻译失败还白白扣点

**预设管理**

```
/nai presets                              查看所有预设（也可用 /nai 预设）
/nai presets <预设名>                      查看单个预设详情（也可用 /nai 预设 <预设名>）
/nai save 我的预设 best quality, detailed  保存自定义预设（也可用 /nai 保存）
/nai update 我的预设 best quality, masterpiece  修改自定义预设（也可用 /nai 修改）
/nai del 我的预设                          删除自定义预设（也可用 /nai 删除）
```

> **小提示**：`save` 命令以「第一个空格」分割名称和质量前缀，所以预设名称不能含空格。
> 例如 `/nai save 我的预设 best quality, absurdres` → 名称=`我的预设`，质量前缀=`best quality, absurdres`。
> 如果想修改已保存的预设，用同样的名称重新保存即可覆盖。

**查询余额** — 查看你的 Nai2API 剩余点数：

```
/nai balance    或  /nai 余额  /nai 点数  /nai 次数
```

返回示例：
```
Nai2API 余额查询
  剩余点数: 86 点
  账号状态: 正常
  ---
  预计可生成:
    普通尺寸(竖图/横图/方图): ~86 张
    2K尺寸: ~5 张
    4K尺寸: ~3 张
```

**内置预设** — 来自 Nai2API 官方前端，和网页版完全一致：

| 预设名 | 说明 |
|--------|------|
| 2.5D唯美风 | Nai2API 默认，半写实半动漫风格 |
| 韩漫小清新风 | 韩式漫画清新风格 |
| 本子动漫风 | 日系本子风格，多画师混合 |
| GalGame风 | 游戏CG风格（ningen_mame 等画师） |
| 动漫风 | 旧版多画师混合动漫风格 |

**支持的模型**

| 模型 | 说明 | 普通图扣点 |
|------|------|-----------|
| `nai-diffusion-5-full` | NovelAI V5 最新（2026-08 上线） | **5** |
| `nai-diffusion-5-curated` | NovelAI V5 精简版 | **5** |
| `nai-diffusion-4-5-full` | NovelAI V4.5 | 1 |
| `nai-diffusion-4-5-curated` | NovelAI V4.5 精简版 | 1 |
| `nai-diffusion-4-full` | NovelAI V4 | 1 |
| `nai-diffusion-3` | NovelAI V3 | 1 |
| `nai-diffusion-furry-3` | 兽人风格 V3 | 1 |
| `nai-diffusion-2` | NovelAI V2 | 1 |
| `safe-diffusion` | 安全模式 | 1 |

**尺寸与费用**

⚠️ **扣点由「模型 + 尺寸」共同决定**，不是只看尺寸：

| 尺寸 | 分辨率 | V4.5 系列 | V5 系列 |
|------|--------|----------|---------|
| 竖图 | 832×1216 | 1 | **5** |
| 横图 | 1216×832 | 1 | **5** |
| 方图 | 1024×1024 | 1 | **5** |
| 2K竖图 | 1088×1600 | 15 | 15 |
| 2K横图 | 1600×1088 | 15 | 15 |
| 2K方图 | 1344×1344 | 15 | 15 |
| 4K竖图 | 1344×1984 | 25 | 25 |
| 4K横图 | 1984×1344 | 25 | 25 |
| 4K方图 | 1728×1728 | 25 | 25 |

> **注意**：V5 的普通尺寸就是 5 点，不是 1 点。想省钱画普通图就用 V4.5 系列。
> 另外 V5 没有专属分辨率，和 V4.5 共用上面这套尺寸表。

**高扣点二次确认**

为了防止误扣，下面这些情况会先问你一次（回复「确认」才生成）：

- 2K 尺寸（15 点）
- 4K 尺寸（25 点）
- **V5 模型的普通尺寸（5 点）**

```
/nai 1girl, silver hair
→ ⚠️ 本次使用模型「nai-diffusion-5-full」的普通尺寸，将消耗 5 点（普通尺寸的 V4.5 模型只扣 1 点）。
  确定要生成吗？10 分钟内回复「确认」继续，回复「取消」放弃。
```

> - 只有回复「确认」才会扣点，回复「取消」没有任何消耗
> - 10 分钟不回复自动作废；期间发别的指令也会放弃这次确认
> - 不想要提示？把 `confirm_hd_size` 关掉；或把默认模型换成 V4.5 系列

---

## 人格提示词（生图助手）

本插件配套一个 AstrBot 人格提示词，让 AI 助手自动帮你画图。
文件位置：`persona/nai_artist_persona.md`

人格提示词包含：
- 角色设定（活泼有耐心的 AI 生图助手）
- 中文描述 → NovelAI 英文标签的翻译规则
- 提示词结构和质量词排序技巧
- nai_generate 工具调用规范（何时调用、参数如何填）
- 回复格式（调用前/成功/失败如何回应用户）
- 多轮对话迭代技巧（改姿势/换场景/调风格/保持 seed 微调）
- 常用提示词模板（二次元/写实/风景/GalGame CG）
- 常见元素中英文对照表（发色/发型/瞳色/服装/表情/动作/场景/光影/质量词）

### 配置步骤

**步骤 1**：确保插件配置中 `llm_tool_enabled = true`（默认已开启）

**步骤 2**：打开 AstrBot 管理后台 → 找到「人格与情景」或「Persona」面板

**步骤 3**：新建一个人格，起个名字（如「生图助手」或「AI 画师」）

**步骤 4**：将 `persona/nai_artist_persona.md` 的内容复制粘贴到人格的 System Prompt 中

**步骤 5**：将这个人格设为默认人格，或在对话中切换到该人格

**步骤 6**：直接用中文跟 AI 说你想要什么，它会自动生成图片

### 对话示例

```
用户：帮我画一个银发蓝眼的女孩，站在雨夜霓虹街头
AI：好的，正在为你生成... ✨
    [自动调用 nai_generate 工具]
    [图片发送给用户]
    这是根据你的描述生成的图片，喜欢吗？😊

用户：换个横图风景的，日落海边
AI：收到，马上画一张日落海景 🎨
    [自动调用 nai_generate 工具，size="横图"]
    [图片发送给用户]
    画面中你最满意的部分是哪里？如果想调整可以告诉我～

用户：再画一张 GalGame 风格的校服女生
AI：来啦，正在用 AI 画笔创作... 🖌️
    [自动调用 nai_generate 工具，preset="GalGame风"]
    [图片发送给用户]
    这是第 1 张，想要不同风格/姿势/服装可以继续说！
```

### 小技巧

- 想改图？可以对 AI 说"换个姿势"、"换个场景"、"更写实一点"等
- 想提高清晰度？说"来一张 2K 高清版"或"4K 超清版"
- 想微调但保持画面风格？让 AI 记录同一个 seed 再调整
- 想探索不同风格？问 AI"这个预设里有什么风格？"

---

## LLM 工具调用（开发者参考）

当 `llm_tool_enabled` 开启时，AI 助手可通过以下工具调用：

### nai_generate - 生成图片

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 图片描述，**中文或英文都可以**（会自动直译为英文标签） |
| `size` | string | 否 | 尺寸，如"竖图"、"横图"、"2K竖图"等 |
| `artist` | string | 否 | 质量前缀/画师串，如"best quality, absurdres" |
| `negative` | string | 否 | 负面提示词，留空用默认 |
| `preset` | string | 否 | 预设名称，如"高质量"、"动漫风"、"GalGame风" |
| `seed` | int | 否 | 随机种子，0 表示自动随机 |
| `model` | string | 否 | 模型，如"5"、"4.5"、"furry"，留空用默认 |

**参数优先级**：`artist` > `preset` > 默认（2.5D唯美风）

### nai_get_balance - 查询余额

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 无 | - | - | 直接调用，返回账户余额和可生成图片数量 |

### nai_list_presets - 预设管理

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `preset_name` | string | 否 | 指定预设名称查看详情，留空列出所有预设 |

### nai_save_preset - 保存预设

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 预设名称（不能含空格） |
| `artist` | string | 是 | 质量前缀/画师串 |

### nai_delete_preset - 删除预设

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 要删除的预设名称（内置预设无法删除） |

---

## 参数优先级（指令方式）

`--artist` > `-p 预设` > 配置中的 `default_artist`

即手动指定的 `--artist` 会覆盖预设，预设会覆盖默认配置。

---

## 目录结构

```
astrbot_plugin_nai_plus/
├── main.py                 # 插件入口，命令注册和 LLM Tool 注册
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # 配置 Schema
├── requirements.txt        # 依赖声明
├── README.md               # 本文件
├── core/
│   ├── nai2api_client.py   # Nai2API 客户端（/generate 请求、扣点计算）
│   ├── translate_manager.py # 提示词直译（词库直译 + 模型翻译 + 结果校验）
│   ├── prompt_dict.py      # 词库加载、切分与替换
│   ├── prompt_dict/        # 内置词库（角色/外观/场景，共 1572 条）
│   │   ├── characters.json
│   │   ├── appearance.json
│   │   └── scene.json
│   ├── img2img_client.py   # 图生图（通用 HTTP 模板）
│   ├── image_manager.py    # 图片保存和缓存管理
│   ├── group_blacklist.py  # 群聊黑名单（群号解析 + 命中判断）
│   └── preset_manager.py   # 预设加载和保存
└── persona/
    └── nai_artist_persona.md  # 生图助手人格提示词（System Prompt）
```

> **想加自己的词条**：不要改 `core/prompt_dict/` 里的文件（插件更新会覆盖）。
> 新建一个自己的 `.json` 或 `.txt`，把路径填进配置的「自定义词库文件路径」即可，
> 格式见上方「[词库](#词库提示词直译加速)」章节。
