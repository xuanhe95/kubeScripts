import argparse
import subprocess
import json
import re

# ANSI escape codes for colors
PRIMARY_COLOR = "\033[93m"  # 亮黄色
SECONDARY_COLOR = "\033[93m"  # 亮黄色
THIRDARY_COLOR = "\033[92m"  # 亮绿色
RESET_COLOR = "\033[0m"


def get_nodes_with_label(label):
    """Get all node names and IPs with a specific label."""
    result = subprocess.run(
        ["kubectl", "get", "nodes", "-l", label, "-o", "json"],
        capture_output=True,
        text=True,
    )
    nodes = json.loads(result.stdout)["items"]
    return [
        (
            node["metadata"]["name"],
            next(
                addr["address"]
                for addr in node["status"]["addresses"]
                if addr["type"] == "InternalIP"
            ),
        )
        for node in nodes
    ]


def find_resource_names(node_name, resource_keyword):
    """Use regex to find resources in the node that match the specified keyword."""
    result = subprocess.run(
        ["kubectl", "get", "node", node_name, "-o", "json"],
        capture_output=True,
        text=True,
    )
    node_info = json.loads(result.stdout)
    
    allocatable_resources = node_info["status"]["allocatable"]
    matched_resources = {
        resource_name: int(amount) for resource_name, amount in allocatable_resources.items()
        if re.search(resource_keyword, resource_name, re.IGNORECASE)
    }
    
    return matched_resources


def get_used_resources(node_name, matched_resources):
    """Calculate requested resources for both active and all tasks on the specified node, and list pods using specified resources."""
    result = subprocess.run(
        ["kubectl", "get", "pods", "--all-namespaces", "-o", "json"],
        capture_output=True,
        text=True,
    )
    pods = json.loads(result.stdout)["items"]

    used_resources_active = {res: 0 for res in matched_resources}  # 排除 Complete 状态的资源请求
    used_resources_all = {res: 0 for res in matched_resources}     # 包括所有任务的资源请求
    resource_using_pods = {res: [] for res in matched_resources}   # 存储使用指定资源的 Pod 信息

    for pod in pods:
        if pod["spec"].get("nodeName") == node_name:
            for container in pod["spec"]["containers"]:
                for resource_name in matched_resources:
                    resource_request = (
                        container.get("resources", {})
                        .get("requests", {})
                        .get(resource_name)
                    )
                    if resource_request:
                        used_resources_all[resource_name] += int(resource_request)
                        if pod["status"]["phase"] == "Running":
                            used_resources_active[resource_name] += int(resource_request)
                            resource_using_pods[resource_name].append(
                                f'{pod["metadata"]["namespace"]}/{pod["metadata"]["name"]} requests {resource_name}: {resource_request}'
                            )

    return used_resources_active, used_resources_all, resource_using_pods


def main(label, resource_keyword):
    print("Retrieving resource information for nodes with label:", label)
    print("--------------------------------------------------------")

    nodes = get_nodes_with_label(label)
    overall_totals = {}
    overall_availables_excluding_complete = {}
    overall_availables_including_complete = {}
    available_nodes_excluding_complete = []
    available_nodes_including_complete = []

    for node_name, node_ip in nodes:
        matched_resources = find_resource_names(node_name, resource_keyword)
        
        if not matched_resources:
            print(f"No resources found matching keyword '{resource_keyword}' on node {node_name}.")
            continue

        print(f"\nNode: {node_ip} ({node_name})")
        node_totals = {res: matched_resources[res] for res in matched_resources}
        used_resources_active, used_resources_all, resource_using_pods = get_used_resources(node_name, matched_resources)

        for resource_name, total_amount in node_totals.items():
            used_active = used_resources_active[resource_name]
            used_all = used_resources_all[resource_name]

            available_excluding_complete = total_amount - used_active
            available_including_complete = total_amount - used_all

            print(
                f"  Resource: {resource_name}"
            )
            print(
                f"    Available: {PRIMARY_COLOR}{available_excluding_complete}{RESET_COLOR}, {SECONDARY_COLOR}{available_including_complete}{RESET_COLOR} (Include Complete Tasks)"
            )
            print(
                f"    Total: {total_amount}   Used (Active): {used_active}   Used (All): {used_all}"
            )

            # 更新 overall summary
            overall_totals[resource_name] = overall_totals.get(resource_name, 0) + total_amount
            overall_availables_excluding_complete[resource_name] = (
                overall_availables_excluding_complete.get(resource_name, 0) + available_excluding_complete
            )
            overall_availables_including_complete[resource_name] = (
                overall_availables_including_complete.get(resource_name, 0) + available_including_complete
            )

            # 添加有可用资源的节点到列表
            if available_excluding_complete > 0:
                available_nodes_excluding_complete.append(
                    (node_ip, node_name, resource_name, available_excluding_complete)
                )
            if available_including_complete > 0:
                available_nodes_including_complete.append(
                    (node_ip, node_name, resource_name, available_including_complete)
                )

            # 输出占用对应资源的 Pod 列表
            if resource_using_pods[resource_name]:
                print(f"    Pods using {resource_name}:")
                for pod in resource_using_pods[resource_name]:
                    print(f"      {THIRDARY_COLOR}{pod}{RESET_COLOR}")
            else:
                print(f"    No Pods are using {resource_name}.")

        print("--------------------------------------------------------")

    # 输出总结
    print(f"\n{PRIMARY_COLOR}Summary across all nodes:{RESET_COLOR}")
    print("--------------------------------------------------------")
    for resource_name in overall_totals:
        total = overall_totals[resource_name]
        available_excluding_complete = overall_availables_excluding_complete[resource_name]
        available_including_complete = overall_availables_including_complete[resource_name]

        utilization_rate_excluding = 1 - (available_excluding_complete / total) if total else 0
        utilization_rate_including = 1 - (available_including_complete / total) if total else 0

        print(f"Resource: {resource_name}")
        print(
            f"  Total: {total}"
            f"  Available (Excluding Complete): {PRIMARY_COLOR}{available_excluding_complete}{RESET_COLOR}, Utilization: {THIRDARY_COLOR}{utilization_rate_excluding:.2%}{RESET_COLOR}"
        )
        print(
            f"  Available (Including Complete): {SECONDARY_COLOR}{available_including_complete}{RESET_COLOR}, Utilization: {THIRDARY_COLOR}{utilization_rate_including:.2%}{RESET_COLOR}"
        )
        print("--------------------------------------------------------")

    # 输出可用资源的节点列表
    if available_nodes_excluding_complete:
        print(f"\nNodes with Available Resources (Excluding Complete): {PRIMARY_COLOR}{len(available_nodes_excluding_complete)}{RESET_COLOR}")
        print("--------------------------------------------------------")
        for node_ip, node_name, resource_name, available in available_nodes_excluding_complete:
            print(
                f"Node: {THIRDARY_COLOR}{node_ip}{RESET_COLOR} ({node_name}) - Available {resource_name} (Excluding Complete): {PRIMARY_COLOR}{available}{RESET_COLOR}"
            )
        print("--------------------------------------------------------")
    else:
        print("\nNo nodes with available resources (Excluding Complete).")

    if available_nodes_including_complete:
        print(f"\nNodes with Available Resources (Including Complete): {PRIMARY_COLOR}{len(available_nodes_including_complete)}{RESET_COLOR}")
        print("--------------------------------------------------------")
        for node_ip, node_name, resource_name, available in available_nodes_including_complete:
            print(
                f"Node: {THIRDARY_COLOR}{node_ip}{RESET_COLOR} ({node_name}) - Available {resource_name} (Including Complete): {SECONDARY_COLOR}{available}{RESET_COLOR}"
            )
    else:
        print("\nNo nodes with available resources (Including Complete).")

    print("--------------------------------------------------------")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Retrieve resource usage information for nodes with a specific label and keyword"
    )
    parser.add_argument(
        "label", help="Label to filter nodes, e.g., environment=production"
    )
    parser.add_argument(
        "resource_keyword", help="Keyword to search for in resource names, e.g., 'gpu', 'cpu', 'memory', 'spiderpool'"
    )
    args = parser.parse_args()

    main(args.label, args.resource_keyword)
