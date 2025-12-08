import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
from temporalio.client import CloudOperationsClient
from temporalio.api.cloud.cloudservice.v1 import GetUsageRequest
from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.json_format import MessageToDict


async def main():
    client = await CloudOperationsClient.connect(
        api_key=os.getenv("TEMPORAL_API_KEY"),
        version="v1",
        target_host="saas-api.tmprl.cloud:443",
        tls=True,
    )
    
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    start_time = Timestamp()
    start_time.FromDatetime(today - timedelta(days=10))
    
    end_time = Timestamp()
    end_time.FromDatetime(today)
    
    request = GetUsageRequest(
        start_time_inclusive=start_time,
        end_time_exclusive=end_time,
        page_size=100
    )
    response = await client.cloud_service.get_usage(request)
    print(json.dumps(MessageToDict(response), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
