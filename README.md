# LabReserve — 实验室设备预约系统 (静态版)

纯前端单页应用，使用 localStorage 持久化数据，可直接部署到 GitHub Pages。

## 本地使用

直接用浏览器打开 `docs/index.html` 即可。

## 部署到 GitHub Pages

1. 创建 GitHub 仓库，将本项目推送上去
2. 进入仓库 Settings → Pages
3. Source 选择 **Deploy from a branch**
4. Branch 选择 `main`，文件夹选择 `/docs`
5. 点击 Save，等待部署完成
6. 访问 `https://<你的用户名>.github.io/<仓库名>/`

## 功能

- 设备列表（搜索/筛选/8台预置设备）
- 预约申请（时间冲突自动检测）
- 管理员审批（通过/拒绝/完成归还）
- 设备报损（轻微/严重/致命）
- 使用统计（状态分布/使用排行/审批通过率）
- 添加/编辑/删除设备

## 管理员密码

默认: `admin123`
