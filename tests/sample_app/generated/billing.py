### <django-deluxe-stubs>
# For the IDE and the linters only: the sandbox strips this block and binds these names itself.
# A pydantic model or a dataclass crosses as a dict of its JSON form, not as an instance: where a name below says
# "dict", the class is only there to look the dict's shape up in.
# isort: off
from tests.sample_app.billing import BillUsage
from tests.sample_app.billing import Cost  # a dict in the sandbox
from tests.sample_app.billing import Delivery
from tests.sample_app.billing import Usage  # a dict in the sandbox

# BillUsage: `bill_usage()`
get_cost = BillUsage.get_cost  # dicts in the sandbox: Cost, Usage
get_usage = BillUsage.get_usage  # dicts in the sandbox: Usage
record = BillUsage.record  # dicts in the sandbox: Usage
# isort: on
### </django-deluxe-stubs>


def bill_usage(factor: float) -> Cost:
    usage = get_usage()  # Usage
    video_usage = {
        'details': [token_count for token_count in usage['details'] if token_count['modality'] == 'VIDEO'],
        'created': usage['created'],
    }
    record({'usage': video_usage, 'label': 'video'})
    cost = get_cost(video_usage)  # Cost
    return {'tokens': cost['tokens'], 'usd': cost['usd'] * factor}
