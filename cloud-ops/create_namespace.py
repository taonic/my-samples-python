import asyncio
import os
from temporalio.client import CloudOperationsClient
from temporalio.api.cloud.cloudservice.v1 import CreateNamespaceRequest
from temporalio.api.cloud.namespace.v1 import NamespaceSpec


async def main():
    client = await CloudOperationsClient.connect(
        api_key=os.getenv("TEMPORAL_API_KEY"),
        version="v1",
        target_host="saas-api.tmprl.cloud:443",
        tls=True,
    )
    
    namespace_name = input("Enter new namespace name: ")

    spec = NamespaceSpec(
        name=namespace_name,
        regions=["aws-us-east-1"],
        retention_days=7,
    )
    
    request = CreateNamespaceRequest(spec=spec)
    response = await client.cloud_service.create_namespace(request)
    print(f"Namespace '{response.namespace}' created successfully")


if __name__ == "__main__":
    asyncio.run(main())
