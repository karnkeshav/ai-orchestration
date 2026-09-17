import os, json
from datetime import date, timedelta
from fastmcp import FastMCP

mcp = FastMCP("aws-finops-mcp")


def _client(service, region="us-east-1"):
    import boto3
    return boto3.client(service, region_name=region)


@mcp.tool()
def aws_cost_by_service(days: int = 30) -> str:
    """Real AWS cost breakdown by service for the last N days (default 30), via Cost Explorer.
    Use this instead of a single total whenever the user wants to know WHAT is driving their
    AWS bill, not just how much it is."""
    try:
        ce = _client("ce")
        end = date.today()
        start = end - timedelta(days=days)
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        results = []
        for period in resp.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                amt = group["Metrics"]["UnblendedCost"]
                results.append({
                    "service": group["Keys"][0],
                    "cost": float(amt["Amount"]),
                    "unit": amt["Unit"],
                })
        results.sort(key=lambda r: -r["cost"])
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error querying AWS cost by service: {str(e)}"


@mcp.tool()
def aws_cost_forecast(days: int = 30) -> str:
    """Forecast AWS spend for the next N days via Cost Explorer's forecast API."""
    try:
        ce = _client("ce")
        start = date.today()
        end = start + timedelta(days=days)
        resp = ce.get_cost_forecast(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Metric="UNBLENDED_COST",
            Granularity="MONTHLY",
        )
        total = resp.get("Total", {})
        return json.dumps({
            "forecasted_amount": total.get("Amount"),
            "unit": total.get("Unit"),
            "period": f"{start.isoformat()} to {end.isoformat()}",
        }, indent=2)
    except Exception as e:
        return f"Error forecasting AWS cost: {str(e)}"


@mcp.tool()
def aws_cloudtrail_lookup(event_name: str = None, resource_name: str = None, days: int = 14, limit: int = 20) -> str:
    """Search AWS CloudTrail for root-cause investigation -- who did what action, from which
    identity/IP, and when. Pass event_name (e.g. 'RunInstances', 'CreateBucket',
    'EnableService') and/or resource_name to filter; omit both to get the most recent events."""
    try:
        from datetime import datetime, timezone
        ct = _client("cloudtrail")
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        kwargs = {
            "StartTime": start,
            "EndTime": end,
            "MaxResults": min(limit, 50),
        }
        if event_name:
            kwargs["LookupAttributes"] = [{"AttributeKey": "EventName", "AttributeValue": event_name}]
        elif resource_name:
            kwargs["LookupAttributes"] = [{"AttributeKey": "ResourceName", "AttributeValue": resource_name}]
        resp = ct.lookup_events(**kwargs)
        results = []
        for event in resp.get("Events", [])[:limit]:
            raw = json.loads(event.get("CloudTrailEvent", "{}"))
            results.append({
                "event_time": str(event.get("EventTime")),
                "event_name": event.get("EventName"),
                "principal": raw.get("userIdentity", {}).get("arn") or raw.get("userIdentity", {}).get("principalId"),
                "source_ip": raw.get("sourceIPAddress"),
                "aws_region": raw.get("awsRegion"),
            })
        return json.dumps(results, indent=2) if results else "No matching CloudTrail events found in that window."
    except Exception as e:
        return f"Error querying AWS CloudTrail: {str(e)}"


@mcp.tool()
def aws_freetier_status() -> str:
    """Check whether this AWS account is on the Free or Paid tier plan."""
    try:
        ft = _client("freetier")
        resp = ft.get_account_plan_state()
        return json.dumps(resp.get("accountPlanState", resp), indent=2, default=str)
    except Exception as e:
        return f"Error checking AWS free tier status: {str(e)}"


if __name__ == "__main__":
    mcp.run()
