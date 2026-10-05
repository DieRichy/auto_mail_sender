# HIWIN 自动邮件工具

在本机运行的旅行社合作邮件工具：模板、国家分组、附件、逐封预览与批准、立即／定时发送、回复检测及 Google Sheet 同步。Python 标准库实现，无需安装第三方依赖。

## 启动

需要 Python 3.10 或更新版本（含时区数据库），推荐在 macOS 使用。

```sh
cd lightweight_tool
python3 app.py
```

打开 http://127.0.0.1:8765 。Mac 也可双击 `启动邮件工具.command`。

首次使用请先替换示例联系人、设置员工联系方式，再连接公司邮箱。示例地址不用于实际开发邮件。每封邮件须预览、批准并确认后才发送。邮件密码只保存在当前进程内存中。

## 本机配置

- 复制 `contacts.example.json` 为 `contacts.json`，填入真实机构名单；也可在界面新增或连接 Google Sheet 后刷新联系人。
- 复制 `local_config.example.json` 为 `local_config.json`，填入自己的 Sheet ID。也可用环境变量 `HIWIN_SPREADSHEET_ID`。WhatsApp 默认配置可在文件或界面填写。
- LINE、WhatsApp、署名、二维码通过界面配置。可选的 `sender_contacts.json` 格式见示例；二维码在本机 `assets/`。
- Sheet 同步部署详见[操作说明](lightweight_tool/README.md)。Apps Script 的脚本属性须同时配置 `SPREADSHEET_ID` 和 `BRIDGE_SECRET`。

仓库仅包含程序、初始模板、示例配置与测试。真实收件名单、员工联系配置、二维码、数据库、邮件记录、附件、备份、连接密钥和密码不进入版本库。公开仓库中的初始文案及参考价格可被任何人阅读。

## 测试

```sh
python3 -m unittest discover -s lightweight_tool -p test_app.py -q
```

测试使用临时数据库、示例联系人和模拟 SMTP，不发送真实邮件，不操作本机业务数据。

## 模板

台湾繁体中文和台湾英文版包含大阪卖点、附件说明、LINE、ITF 邀约及电话跟进；通用英文版使用 WhatsApp。宣称附有宣传册／价格表的模板，需要在发送前实际上传并绑定相应附件。

通用模板的 `{{partner_website}}` 自动使用收件人国家对应网址，无需为泰国另建同文模板。仅在正文或附件不同的时候保存国家专用副本。删除模板会解除国家绑定并使旧预览失效；历史记录、附件文件与已发邮件保留。初始模板删除后不会在重启时自动重建，最后一个模板不能删除；有未结束定时任务的模板需先取消任务。
