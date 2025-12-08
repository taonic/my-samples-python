# Cloud Operations

This sample demonstrates creating a Temporal namespace using the Cloud Operations API.

## Setup

Set the following environment variables:

```bash
export TEMPORAL_API_KEY="your-api-key"
export NEW_NAMESPACE_NAME="my-new-namespace"
```

## Run

```bash
uv run python cloud_operations/create_namespace.py
```

## Get Usage

```bash
uv run python cloud-ops/get_usage.py
```

### Sample Output

```json
{
  "summaries": [
    {
      "startTime": "2025-11-28T00:00:00Z",
      "endTime": "2025-11-29T00:00:00Z",
      "recordGroups": [
        {
          "groupBys": [
            {
              "key": "GROUP_BY_KEY_NAMESPACE",
              "value": "dev1.abcde"
            }
          ],
          "records": [
            {
              "type": "RECORD_TYPE_RETAINED_STORAGE",
              "unit": "RECORD_UNIT_BYTE_SECONDS",
              "value": 906508800.0
            }
          ]
        }
      ]
    },
    {
      "startTime": "2025-11-29T00:00:00Z",
      "endTime": "2025-11-30T00:00:00Z",
      "recordGroups": [
        {
          "groupBys": [
            {
              "key": "GROUP_BY_KEY_NAMESPACE",
              "value": "dev1.abcde"
            }
          ],
          "records": [
            {
              "type": "RECORD_TYPE_RETAINED_STORAGE",
              "unit": "RECORD_UNIT_BYTE_SECONDS",
              "value": 906508800.0
            }
          ]
        }
      ]
    }
  ]
}
```
