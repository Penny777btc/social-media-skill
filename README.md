# social-media-skill

个人使用的 Codex skill：把中文 Markdown 文章及配图排成美丫姐风格的微信公众号图文。默认重点色为 `#E62270`，包含开篇/结尾贴纸，以及生成 HTML、Markdown 图片包的脚本。

## 使用

将本仓库放到 `~/.codex/skills/social-media-skill`，在 Codex 中提供文章 Markdown 和同级的「图片和附件」文件夹（或包含二者的 ZIP），并说：

> 使用 $social-media-skill，把这篇文章按公众号样式排版。保留原文和图片顺序，输出 HTML 与 Markdown 图片包。

默认会整理段落和小标题、标注待确认内容，并交付可从浏览器复制的 HTML。发布前请在公众号后台保存草稿并预览图片、GIF 和样式。用户明确指定的重点色、字号和输出形式优先。

`SKILL.md` 是工作流程；`references/layout-rules.md` 是排版规范；`scripts/wx_layout.py` 是不依赖第三方 Python 包的渲染、打包和机械检查工具；`assets/` 存放两张品牌贴纸。无需 OpenAI API Key，内容判断由当前 Codex 会话完成。
