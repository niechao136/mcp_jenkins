from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
import asyncio
import httpx
import xml.etree.ElementTree as ElementTree
from typing import Any, Optional
from urllib.parse import urlencode


class JenkinsClient:
    def __init__(self, jenkins_url: str, user: str, token: str):
        self.base = jenkins_url.rstrip("/")
        self.auth = (user, token)

    async def validate(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base}/crumbIssuer/api/json", auth=self.auth)
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def get_jobs(self) -> list:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.base}/api/json", auth=self.auth)
            response.raise_for_status()
            jobs = response.json().get("jobs", [])
            return [job["name"] for job in jobs]

    async def job_config(self, name: str) -> str:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.base}/job/{name}/config.xml", auth=self.auth)
            response.raise_for_status()
            return response.text

    async def job_info(self, name: str) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.base}/job/{name}/api/json", auth=self.auth)
            response.raise_for_status()
            return response.json()

    async def build_job(self, name: str) -> bool:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(f"{self.base}/job/{name}/build", auth=self.auth)
            response.raise_for_status()
            return response.status_code == 200

    async def build_with_param(self, name: str, param: dict) -> bool:
        query_string = "?" + urlencode(param) if param else ""
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(f"{self.base}/job/{name}/buildWithParameters{query_string}", auth=self.auth)
            response.raise_for_status()
            return response.status_code == 200


async def get_jenkins_client(request: Request) -> JenkinsClient:
    query_params = request.query_params if request else {}

    jenkins_url = query_params.get("jenkins_url")
    if not jenkins_url:
        raise ValueError("缺少参数 jenkins_url")

    jenkins_user = query_params.get("jenkins_user")
    if not jenkins_user:
        raise ValueError("缺少参数 jenkins_user")

    jenkins_token = query_params.get("jenkins_token")
    if not jenkins_token:
        raise ValueError("缺少参数 jenkins_token")

    client = JenkinsClient(jenkins_url=jenkins_url, user=jenkins_user, token=jenkins_token)

    if not client.validate():
        raise ValueError("无法连接 Jenkins，或凭据无效")

    return client


async def parse_parameters(xml_content: str) -> dict[str, Any]:
    root = ElementTree.fromstring(xml_content)
    result = {
        "has_param": False,
        "parameters": []
    }

    # 查找 ParametersDefinitionProperty
    namespace = ""  # Jenkins XML 通常无命名空间
    param_def_property = root.find(f".//{namespace}hudson.model.ParametersDefinitionProperty")
    if param_def_property is None:
        return result  # 没有参数定义

    result["has_param"] = True
    param_definitions = param_def_property.find(f"{namespace}parameterDefinitions")
    if param_definitions is None:
        return result  # 有 property 但无参数

    # 遍历所有参数定义节点
    for param in param_definitions:
        param_info: dict[str, str | list[str]] = {
            "type": param.tag.split("}")[-1],  # 去命名空间
            "name": param.findtext(f"{namespace}name", default="").strip(),
            "description": param.findtext(f"{namespace}description", default="").strip(),
            "defaultValue": param.findtext(f"{namespace}defaultValue", default="").strip()
        }

        # 针对 Choice 类型的参数提取 choices
        if param_info["type"].endswith("ChoiceParameterDefinition"):
            choices = param.find(f"{namespace}choices")
            if choices is not None:
                param_info["choices"] = [choice.text.strip() for choice in choices.findall(f"{namespace}string")]

        result["parameters"].append(param_info)

    return result


class JenkinsMCP(FastMCP):
    async def list_tools(self):
        request: Request = self.session_manager.app.request_context.request

        await get_jenkins_client(request)

        return await super().list_tools()


# Init
mcp = JenkinsMCP("jenkins", stateless_http=True, host="0.0.0.0", port=10080)


@mcp.tool()
async def get_jobs() -> list:
    """列出 Jenkins 中所有的任务。
    执行任务前应先获取所有任务，校验任务名是否存在以及是否需要组参数。
    当任务需要参数，但用户没有提供时，无需确认直接使用参数默认值。
    """
    request: Request = mcp.session_manager.app.request_context.request
    client = await get_jenkins_client(request)
    jobs = await client.get_jobs()

    result = []
    for job in jobs:
        config = await client.job_config(job)
        param = await parse_parameters(config)
        result.append({"name": job, **param})

    return result


@mcp.tool()
async def trigger_build(job_name: str, parameters: Optional[dict] = None) -> dict:
    """执行某个具体 Jenkins 任务的构建。
    执行任务前应先获取所有任务进行任务名校验和帮助构建参数。
    当任务需要参数，但用户没有提供时，无需确认直接使用参数默认值。

    参数：
        job_name：需要执行的任务名称
        parameters：执行构建需要的参数对象（例如：{"param1": "value1"}）

    返回结果：
        构建信息对象，包括构建序号
    """
    if not isinstance(job_name, str):
        raise ValueError(f"job_name must be a string, got {type(job_name)}")
    if parameters is not None and not isinstance(parameters, dict):
        raise ValueError(f"parameters must be a dictionary or None, got {type(parameters)}")

    request: Request = mcp.session_manager.app.request_context.request
    client = await get_jenkins_client(request)
    info = await client.job_info(job_name)
    next_build_number = info["nextBuildNumber"]
    config = await client.job_config(job_name)
    param = await parse_parameters(config)
    if param["has_param"]:
        await client.build_with_param(job_name, parameters)
    else:
        await client.build_job(job_name)

    await asyncio.sleep(5)

    return {
        "job_name": job_name,
        "job_url": f"{client.base}/job/{job_name}",
        "build_number": next_build_number,
        "build_url": f"{client.base}/job/{job_name}/{next_build_number}",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
