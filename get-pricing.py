import boto3

# Get resources in the group
rg_client = boto3.client('resource-groups')
resources = rg_client.list_group_resources(Group='byoc-by-tim')

# Query costs by the tag that defines your group
ce_client = boto3.client('ce')
response = ce_client.get_cost_and_usage(
    TimePeriod={'Start': '2026-03-01', 'End': '2026-03-19'},
    Granularity='MONTHLY',
    Metrics=['UnblendedCost'],
    Filter={
        'Tags': {
            'Key': 'your-tag-key',
            'Values': ['your-tag-value']
        }
    }
)
print(response['ResultsByTime'])
