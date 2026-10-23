# 口播视频剪辑总台 · Koubo Video Editor

中文口播视频剪辑 skill 与本地工具集：把语义剪辑、锁定时间轴、字幕词证据和有限的图层合成串联起来，供支持 skill 的编码助手使用，也可直接运行 Python 工具。

**初始版本。** 维护者：Koma Lee。当前没有可核验的公开采用度指标。

## 能做什么

- 从人工审核的保留区间构建帧对齐时间轴，检查被替代复录是否仍残留。
- 用最终PCM音频哈希与时间轴指纹绑定词时间，拒绝缺失、过期或改词的字幕证据。
- 制作透明字幕层、定时文字/图片、画框、手动关键帧和已有音频的混音。
- 分开记录技术检查、字幕证据、视觉播放和听审。

脚本不自动理解语义，不提供自动剪辑决策、强制对齐模型、封面生成服务或原生剪映工程。ASR、图像生成、字体与素材需要自行提供。macOS人像遮罩后端仅支持整数CFR；基础时间轴可保留59.94等分数帧率。

## 运行环境

- Python 3.10+（使用该版本起支持的标准库API）。
- `ffmpeg`、`ffprobe` 在 PATH 中，FFmpeg 构建含 libx264、常用缩放/叠加滤镜和相关解码器。
- 画面/字幕工具需要 Pillow：`python3 -m pip install -r requirements.txt`。建议先在虚拟环境安装。
- 中文字幕需要本机可用的中文字体，必要时用 `--font` 指定；字体许可独立于项目许可。
- 可选：macOS Vision + Swift 用于人像遮罩；已有 Whisper 或 MLX Whisper 与本地模型用于转录。本仓库不下载或分发模型。

安装FFmpeg、字体、模型的方式取决于系统。先检查 `python3 --version`、`ffmpeg -version`、`ffprobe -version`，不需要API密钥即可运行本地合成示例。

## 安装为 skill

确认许可与来源后，将仓库中的 `SKILL.md`、`agents/`、`scripts/`、`references/`、`assets/` 一起复制到你的 skill 目录内的 `koubo-video-editor/` 文件夹。已有同名 skill 时先备份，不直接覆盖。宿主的 skill 发现方式以其实际支持为准；仓库中不含自动安装器。

在支持该语法的宿主中可以使用：

> 请使用 $koubo-video-editor 分析这条口播，只给剪辑方案。

> 请使用 $koubo-video-editor 剪辑我提供的视频，保留自然原声，交付SRT并标出未完成的检查。

## 两分钟验证

在仓库根目录运行（输出目录必须尚不存在）：

```sh
python3 -m unittest discover -s tests -v
python3 examples/run_demo.py --out work/demo
```

示例仅用FFmpeg测试图、程序生成色块和测试音，依次执行精剪、静态效果、动态图层、字幕烧录和媒体校验。不含真人、客户资料、第三方视频或字体文件。示例文字是显式未对齐草稿，没有人声，不用来证明语音识别或字幕同步能力。生成的媒体留在指定工作目录，不应提交到仓库。

本次实际执行结果、测试环境和未验证范围见 [验证记录](VALIDATION.md)。其他系统和版本仍需各自验证。

## 处理自己的素材

1. 人工理解和核对素材，写入保留区间；参考 `examples/edit.example.json` 和 [计划格式](references/plan-schema.md)。
2. 使用绝对路径执行计划与底片命令：

```sh
python3 scripts/video_pipeline.py probe /absolute/source.mp4
python3 scripts/video_pipeline.py plan /absolute/edit.json --out /absolute/work/plan.json
python3 scripts/video_pipeline.py render-base /absolute/work/plan.json --out /absolute/work/base.mp4 --work /absolute/work/cache
```

3. 在底片返回的 `voiceReference` 上获得词时间、复核后构建字幕，见 [同步工作流](references/subtitle-sync-and-motion.md)。没有证据的字幕只可作为工作预览。
4. 按需实现 [效果](references/effects-schema.md)、[图层](references/layers-schema.md)，再加普通字幕；用 [本地检查](references/local-workflow.md) 记录实际检查范围。

## 隐私与分发范围

所附工具没有素材上传步骤。本地ASR库与其他可选工具的行为由其自身实现和配置决定；只使用已有本地模型，处理前确认实际依赖。不要把原片、转录、词证据、绝对路径、客户信息或模型权重上传到公开issue。

这个发布包移除了私人封面图、第三方主页截图、账号研究索引与私人项目验证记录。仅包含文本、脚本和可重新生成的合成示例；见 [来源与依赖](NOTICE.md)。

## 维护与贡献

缺陷报告请附操作系统、Python/FFmpeg/Pillow版本、命令、预期/实际行为及最小合成复现。不要附客户原片或凭据。改动需通过单元测试；涉及媒体流程时运行合成示例。细节见 [贡献指南](CONTRIBUTING.md)。

## 许可

本项目由权利人确认按 [MIT 许可证](LICENSE) 发布。第三方工具、模型、字体及用户素材不受此项目的MIT许可授予。
