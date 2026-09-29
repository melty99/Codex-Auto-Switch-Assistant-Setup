# 发布包隐私检查 / Release privacy check

适用版本：v1.0（插件版本 1.0.0）。本记录说明检查范围，不构成绝对匿名或无泄露保证。

已检查发布目录中的文件及最终 ZIP 内容：

- 未发现个人账号数据库、登录配置、令牌、密钥、任务队列、聊天记录或运行日志。
- 未发现开发设备的用户目录绝对路径、实际邮箱、Windows 用户 SID；与本机托管账号名称及标识逐项比对，未命中。
- 扫描命中的示例邮箱、虚构聊天 ID、版本号及 Windows 接口 GUID 均已人工核对，不是实际账号或聊天标识。
- 图像为程序图标及其源素材；未发现 EXIF/XMP 信息，保留的 ICC 块为颜色配置。已查看程序图标，未见账号或聊天信息。
- ZIP 不携带原文件修改时间、注释或额外字段；以固定时间写入。包内未包含开发截图、快捷方式、编译缓存或个人运行数据。
- README 与插件清单中的作者署名是作者主动公开的信息，按要求保留；图标素材和应用适配版本也会保留。

使用后在本机生成的设置、聊天摘要和任务文本不属于此发布包。请勿将后续生成的运行数据加入公开压缩包。SHA256 文件用于完整性核对，不是数字签名或隐私证明。

English: The v1.0 release was checked for local paths, real account identifiers, email addresses, credentials, runtime records and image metadata. No private user data was found within this inspection scope. Matches were reviewed as test fixtures, version numbers or Windows interface identifiers. Author attribution is intentionally public. ZIP file metadata is normalized. This check is not an absolute anonymity guarantee; do not add runtime data generated after installation to a public archive.
