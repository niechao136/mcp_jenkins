
# mcp_jenkins

控制 Jenkins 的 MCP Server

## 发布命令

```shell
# 先停止
docker-compose down --rmi local -v
# 再启动
docker-compose up --build -d
```

## 配置方式

```json
{
  "mcpServers": {
    "jenkins": {
      "url": "http://[服务器IP]:10080/mcp?jenkins_url=[Jenkins地址]&jenkins_user=[Jenkins用户名]&jenkins_token=[Jenkins Token]"
    }
  }
}
```

