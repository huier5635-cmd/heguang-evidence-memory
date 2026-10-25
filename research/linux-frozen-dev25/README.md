# Linux验证草案：仅冻结公开修复材料

目标是为此前Windows测试标识不匹配导致的无法评分取得独立证据，不构成官方赛事提交。原三题禁止重生成，目标答案补丁禁止读取。

仅标准公开仓库Linux runner；权限为空，不使用仓库/赛事/服务器密钥，不上传计费Artifacts。固定提交下载，官方说明的starryzhang实例镜像下载后解析为唯一image_id；同题各组使用同一image_id，输出镜像摘要。先核对HEAD为冻结base_commit及全部原文件摘要，应用原测试补丁和原模型补丁。每组独立容器，禁网络、全部capabilities关闭、禁止提权、2CPU/6GB/256进程；每组最多900秒。执行完整pytest -rA，逐节点结果完整保留，不按摘要ID缩减选择测试。

当前仅完成本地脚本和工作流草案。尚未发布工作流、启动云作业或取得Linux修复率。工作流只在专用coding-linux-frozen-dev25分支触发，禁止更新生产分支；Full始终关闭。

上下游许可证：测试和修复差异来自对应公开项目（pylint-dev/pylint、sphinx-doc/sphinx、pydata/xarray），遵循各项目许可证；生成补丁保留原内容且事前审查通过，执行验证不表示正确性。
