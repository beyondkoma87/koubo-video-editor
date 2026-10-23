# 贡献与缺陷报告

仓库：[beyondkoma87/koubo-video-editor](https://github.com/beyondkoma87/koubo-video-editor)。请通过仓库的issue或pull request报告问题和提交改动。

请提交最小合成复现、操作系统和Python/FFmpeg/Pillow版本、完整命令及预期/实际行为。不要提供密钥、个人联系方式、客户原片、完整转录或含私人绝对路径的日志；必要时先替换为合成素材和通用路径。

修改脚本后运行 `python3 -m unittest discover -s tests -v`。修改渲染行为时运行 `python3 examples/run_demo.py --out work/demo-unique`，核对帧数、PTS和音轨，再按改动范围做视觉与声音审阅。ASR/真实人像遮罩需另附实际条件下的验证，合成图不能证明语义或分割质量。

问题报告不意味着授权上传、扫描或发布他人素材。提交的原创改动须有权按本项目的MIT许可分发。
