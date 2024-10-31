import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
import subprocess

# ANSI escape codes for colors
PRIMARY_COLOR = "\033[93m"  # 亮黄色
SECONDARY_COLOR = "\033[93m"  # 亮黄色
THIRDARY_COLOR = "\033[92m"  # 亮绿色
RESET_COLOR = "\033[0m"


def fetch_data():
    """Fetch all nodes and pods data in one go."""
    nodes_result = subprocess.run(
        ["kubectl", "get", "nodes", "-o", "json"],
        capture_output=True,
        text=True,
    )
    pods_result = subprocess.run(
        ["kubectl", "get", "pods", "--all-namespaces", "-o", "json"],
        capture_output=True,
        text=True,
    )
    nodes_data = json.loads(nodes_result.stdout)["items"]
    pods_data = json.loads(pods_result.stdout)["items"]
    return nodes_data, pods_data


def get_nodes_with_label(nodes_data, label):
    """Filter nodes by label."""
    return [
        (
            node["metadata"]["name"],
            next(
                addr["address"]
                for addr in node["status"]["addresses"]
                if addr["type"] == "InternalIP"
            ),
            node,
        )
        for node in nodes_data
        if any(
            re.fullmatch(label, f"{k}={v}")
            for k, v in node["metadata"].get("labels", {}).items()
        )
    ]


def parse_resource_amount(amount):
    """Extract the numeric part of the resource amount, ignoring units."""
    # Remove all non-digit characters
    numeric_part = re.sub(r"[^\d]", "", amount)
    if not numeric_part:
        raise ValueError(f"Invalid resource amount format: {amount}")
    return int(numeric_part)


def find_resource_names(node_data, resource_keyword):
    """Find resources in the node matching the specified keyword."""
    allocatable_resources = node_data["status"]["allocatable"]
    matched_resources = {}

    for resource_name, amount in allocatable_resources.items():
        if re.search(resource_keyword, resource_name, re.IGNORECASE):
            try:
                # Attempt to parse resource amount, ignoring units
                matched_resources[resource_name] = parse_resource_amount(amount)
            except ValueError:
                # If parsing fails, retain the original string value for display only
                matched_resources[resource_name] = amount

    return matched_resources


def get_used_resources(node_name, pods_data, matched_resources):
    """Calculate requested resources for active and all tasks on the specified node."""
    used_resources_active = {
        res: 0 for res in matched_resources if isinstance(matched_resources[res], int)
    }
    used_resources_all = {
        res: 0 for res in matched_resources if isinstance(matched_resources[res], int)
    }
    resource_using_pods = {res: [] for res in matched_resources}

    for pod in pods_data:
        if pod["spec"].get("nodeName") == node_name:
            for container in pod["spec"]["containers"]:
                for resource_name, total_amount in matched_resources.items():
                    # Only proceed if the total_amount is an integer (skipping string values)
                    if isinstance(total_amount, int):
                        resource_request = (
                            container.get("resources", {})
                            .get("requests", {})
                            .get(resource_name)
                        )
                        if resource_request:
                            try:
                                used_request = parse_resource_amount(resource_request)
                                used_resources_all[resource_name] += used_request
                                if pod["status"]["phase"] == "Running":
                                    used_resources_active[resource_name] += used_request
                                    resource_using_pods[resource_name].append(
                                        f'{pod["metadata"]["namespace"]}/{pod["metadata"]["name"]} requests {resource_name}: {resource_request}'
                                    )
                            except ValueError:
                                # Skip if the request value cannot be converted
                                pass

    return used_resources_active, used_resources_all, resource_using_pods


def process_node(node_info, pods_data, resource_keyword):
    node_name, node_ip, node_data = node_info
    matched_resources = find_resource_names(node_data, resource_keyword)

    if not matched_resources:
        return None

    used_resources_active, used_resources_all, resource_using_pods = get_used_resources(
        node_name, pods_data, matched_resources
    )

    node_summary = {
        "node_name": node_name,
        "node_ip": node_ip,
        "resources": [],
    }

    for resource_name, total_amount in matched_resources.items():
        if isinstance(total_amount, int):  # Only proceed if total_amount is an integer
            used_active = used_resources_active.get(resource_name, 0)
            used_all = used_resources_all.get(resource_name, 0)

            available_excluding_complete = total_amount - used_active
            available_including_complete = total_amount - used_all

            resource_summary = {
                "resource_name": resource_name,
                "total": total_amount,
                "available_excluding_complete": available_excluding_complete,
                "available_including_complete": available_including_complete,
                "used_active": used_active,
                "used_all": used_all,
                "using_pods": resource_using_pods[resource_name],
            }
        else:
            # For non-integer resources, only display total and using pods info
            resource_summary = {
                "resource_name": resource_name,
                "total": total_amount,
                "available_excluding_complete": "N/A",
                "available_including_complete": "N/A",
                "used_active": "N/A",
                "used_all": "N/A",
                "using_pods": resource_using_pods[resource_name],
            }
        node_summary["resources"].append(resource_summary)

    return node_summary


def main(label, resource_keyword):
    nodes_data, pods_data = fetch_data()
    nodes = get_nodes_with_label(nodes_data, label)
    overall_summary = {
        "nodes": [],
        "totals": {},
        "availables_excluding_complete": {},
        "availables_including_complete": {},
    }

    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(process_node, node_info, pods_data, resource_keyword)
            for node_info in nodes
        ]
        for future in futures:
            node_summary = future.result()
            if not node_summary:
                continue

            print("--------------------------------------------------------")
            print(f"\nNode: {node_summary['node_ip']} ({node_summary['node_name']})")
            for resource in node_summary["resources"]:
                print(f"  Resource: {resource['resource_name']}")
                print(
                    f"    Available: {PRIMARY_COLOR}{resource['available_excluding_complete']}{RESET_COLOR}, "
                    f"{SECONDARY_COLOR}{resource['available_including_complete']}{RESET_COLOR} (Include Complete Tasks)"
                )
                print(
                    f"    Total: {resource['total']}   Used (Active): {resource['used_active']}   Used (All): {resource['used_all']}"
                )
                if resource["using_pods"]:
                    print(f"    Pods using {resource['resource_name']}:")
                    for pod in resource["using_pods"]:
                        print(f"      {THIRDARY_COLOR}{pod}{RESET_COLOR}")
                else:
                    print(f"    No Pods are using {resource['resource_name']}.")

                # Update overall summary if total is an integer
                if isinstance(resource["total"], int):
                    overall_summary["totals"][resource["resource_name"]] = (
                        overall_summary["totals"].get(resource["resource_name"], 0)
                        + resource["total"]
                    )
                    overall_summary["availables_excluding_complete"][
                        resource["resource_name"]
                    ] = (
                        overall_summary["availables_excluding_complete"].get(
                            resource["resource_name"], 0
                        )
                        + resource["available_excluding_complete"]
                    )
                    overall_summary["availables_including_complete"][
                        resource["resource_name"]
                    ] = (
                        overall_summary["availables_including_complete"].get(
                            resource["resource_name"], 0
                        )
                        + resource["available_including_complete"]
                    )

            overall_summary["nodes"].append(node_summary)

    # Print overall summary
    print(f"\n{PRIMARY_COLOR}Summary across all nodes:{RESET_COLOR}")
    print("--------------------------------------------------------")
    for resource_name in overall_summary["totals"]:
        total = overall_summary["totals"][resource_name]
        available_excluding_complete = overall_summary["availables_excluding_complete"][
            resource_name
        ]
        available_including_complete = overall_summary["availables_including_complete"][
            resource_name
        ]

        utilization_rate_excluding = (
            1 - (available_excluding_complete / total) if total else 0
        )
        utilization_rate_including = (
            1 - (available_including_complete / total) if total else 0
        )

        print(f"Resource: {resource_name}")
        print(
            f"  Total: {total}"
            f"  Available (Excluding Complete): {PRIMARY_COLOR}{available_excluding_complete}{RESET_COLOR}, Utilization: {THIRDARY_COLOR}{utilization_rate_excluding:.2%}{RESET_COLOR}"
        )
        print(
            f"  Available (Including Complete): {SECONDARY_COLOR}{available_including_complete}{RESET_COLOR}, Utilization: {THIRDARY_COLOR}{utilization_rate_including:.2%}{RESET_COLOR}"
        )
        print("--------------------------------------------------------")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Retrieve resource usage information for nodes with a specific label and keyword"
    )
    # 定义可选参数 -l 和 -r
    parser.add_argument(
        "-l", "--label", help="Label to filter nodes, e.g., environment=production"
    )
    parser.add_argument(
        "-r",
        "--resource_keyword",
        help="Keyword to search for in resource names, e.g., 'gpu', 'cpu', 'memory', 'spiderpool'",
    )
    # 定义位置参数
    parser.add_argument(
        "positional_resource_keyword", nargs="?", help="Resource keyword (positional)"
    )
    parser.add_argument(
        "positional_label", nargs="?", help="Label to filter nodes (positional)"
    )

    args = parser.parse_args()

    # 根据是否使用了-l和-r来确定使用哪组参数
    label = args.label if args.label else args.positional_label
    resource_keyword = (
        args.resource_keyword
        if args.resource_keyword
        else args.positional_resource_keyword
    )

    # 检查是否提供了必要的参数
    if not label or not resource_keyword:
        parser.error("Both label and resource_keyword are required.")

    main(label, resource_keyword)
