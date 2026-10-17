# 本次验证记录

- Python 3.10.12 / Django 5.2.18，SQLite 开发数据库。
- `python manage.py test`：47 项，45 通过，2 项 PostgreSQL 并发测试条件跳过。
- `python manage.py check`：通过，无问题。
- `python manage.py makemigrations --check --dry-run`：无模型变更遗漏。
- 两份前端 JavaScript 的 `node --check`：通过。
- Django 测试客户端验证了学生、关联家长和独立管理员页面，以及提交至最终结算的 HTTP 流程。

未验证：PostgreSQL 实际并发、Docker 构建与部署、浏览器视觉与真实媒体播放、真实供应商 AI、OIDC 服务端联调、LibreOffice 文件转换和真实外部福利回调。对应测试需在目标环境完成。默认 mock 审核明确显示模拟标识。

## 学生创建任务功能移除后的验证

全量测试共 85 项：83 项通过，2 项 PostgreSQL 并发测试因环境未配置而跳过。学生首页和任务列表不再出现创建入口，GET/POST 创建路由及创建业务服务均拒绝学生身份；家长布置、学生附件提交和审核结算测试通过。详细输出见 `docs/latest-test.log`。
