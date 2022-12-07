import os
import sys
import json
from envs import Environment_varibles
from multipledispatch import dispatch
from airtable_wrapper import Airtable_Api
from airtable_wrapper import ec2_instances_to_records, security_groups_to_records
from botocore.exceptions import ClientError
from boto3_wrapper import EC2_Boto, flatten

# # Enviroment variables fetched from lambda context


def catch(func, *args,
          handle=lambda e, kwargs=None: print(
              "EXCEPTION:EC2_INSTANCES_DESCRIPTOR", e, kwargs),
          **kwargs):
    try:
        if func is not None and (not isinstance(func, list)):  # Not invocable function
            return func(*args, **kwargs)
    except ClientError as e:
        if "DryRunOperation" not in str(e):
            return handle(e, kwargs)
        return print("INFO", "DryRunOperation Success")
    except TypeError as e:
        return handle(e)
    except KeyError as e:
        return handle(e)
    except Exception as e:
        return handle(e, kwargs)


def set_environment_variables_from_os():
    """
    Set environment variables from os.environ
    """
    Environment_varibles.AIRTABLE_API_KEY = os.environ.get(
        "AIRTABLE_API_KEY")
    Environment_varibles.AIRTABLE_BASE_ID = os.environ.get(
        "AIRTABLE_BASE_ID")
    Environment_varibles.EC2_INSTANCES_TID = os.environ.get(
        "EC2_INSTANCES_TID")
    Environment_varibles.EC2_SECURITY_GROUPS_TID = os.environ.get(
        "EC2_SECURITY_GROUPS_TID")


def init_airtable_api_client():
    """
    Initialize airtable api client
    """
    return Airtable_Api(_base_url=f"https://api.airtable.com/v0/{Environment_varibles.AIRTABLE_BASE_ID}/",
                        _api_key=Environment_varibles.AIRTABLE_API_KEY)


def security_groups_routine(**kwargs):
    airtable_api_client = init_airtable_api_client()
    security_groups_requests = kwargs.get("security_groups_requests")
    # # Fetch security groups requests
    [catch(group.fetch_security_groups) for group in security_groups_requests]

    records = flatten(
        [
            security_groups_to_records(
                groups=groups.security_groups, region=groups.region
            )
            for groups in security_groups_requests
        ]
    )

    print(
        "INFO:EC2_INSTANCES_DESCRIPTOR",
        "Number of scanned security groups:",
        len(records),
    )

    # # Send security groups collected data to Airtable (Upsert)
    catch(
        airtable_api_client.upsert(
            _records=records,
            _table_tid=Environment_varibles.EC2_SECURITY_GROUPS_TID,
            _fields_to_merge_on=["group_id"],
        )
    )


def ec2_instances_routine(**kwargs):
    records = []
    scanned_instances = []

    airtable_api_client = init_airtable_api_client()
    ec2_instances_requests = kwargs.get("ec2_instances_requests")

    # # Fetch ec2 instances
    [catch(request.fetch_ec2_instances) for request in ec2_instances_requests]

    # # Last Scanned Instances
    [catch(scanned_instances.extend, instances_by_region.instances)
     for instances_by_region in ec2_instances_requests]

    # # Create a Tag whit Key 'Description' if it's not already present
    # # Not required by the time, but even if it's present, the filled Description tags won't be override
    [catch(request.create_description_tags)
     for request in ec2_instances_requests]

    # # Transform aws response into airtable records set
    [catch(records.extend, ec2_instances_to_records(
        instances=response.instances,
        region=response.region))
        for response in ec2_instances_requests]

    print("INFO:EC2_INSTANCES_DESCRIPTOR",
          " Total number of scanned ec2 instances:",
          len(records))
    # # Send EC2 instances collected data to Airtable (Upsert)
    catch(airtable_api_client.upsert(_records=records,
                                     _table_tid=Environment_varibles.EC2_INSTANCES_TID,
                                     _fields_to_merge_on=["instance_id"]))
    return scanned_instances


def cronjob_strategy_to_detect_terminated_ec2_instances(_scanned_instances):
    _docummented_instances = []
    _no_more_in_docs_instances = []
    # # Get current documented instances
    airtable_api_client = init_airtable_api_client()
    # # Sorting is not necessary here, but I wanted to test it
    airtable_api_client.get_records(
        _table_tid=Environment_varibles.EC2_INSTANCES_TID,
        _view='Grid_view',
        _fields=['instance_id', 'instance_name', 'start_date'],
        _sorts=[{'field': 'start_date', 'direction': 'desc'},
                {'fields': 'instance_name', 'direction': 'asc'}])


@ dispatch(dict, object)
def ec2_instances_desc(event, context):
    """
    EC2 instances descriptor lambda invocable function.
    """
    set_environment_variables_from_os()
    available_regions = EC2_Boto.get_available_regions_names()
    # # EC2 describe_instances request list
    boto_requests = [EC2_Boto(region_name=region)
                     for region in available_regions]

    security_groups_routine(security_groups_requests=boto_requests)
    ec2_instances_routine(ec2_instances_requests=boto_requests)

    return {"status code": 200, "body": json.dumps("Scan End V1.1")}


@ dispatch()
def ec2_instances_desc():
    """
    EC2 instances descriptor local invocable function.
    """
    available_regions = EC2_Boto.get_available_regions_names()
    # # EC2 describe_instances request list
    boto_requests = [EC2_Boto(region_name=region)
                     for region in available_regions]
    # security_groups_routine(security_groups_requests=boto_requests)
    scanned_instances = ec2_instances_routine(
        ec2_instances_requests=boto_requests)
    cronjob_strategy_to_detect_terminated_ec2_instances(scanned_instances)

    # cronjob_strategy_to_detect_terminated_ec2_instances(scanned_instances)


def main(**kwargs):
    ec2_instances_desc()


if __name__ == '__main__':
    main(**dict(arg.split('=') for arg in sys.argv[1:]))
