import argparse
import subprocess
import json

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


def get_total_gpu(node_name):
    """Get the total number of GPUs allocatable on the specified node."""
    result = subprocess.run(
        ["kubectl", "get", "node", node_name, "-o", "json"],
        capture_output=True,
        text=True,
    )
    node_info = json.loads(result.stdout)
    # Make sure 'nvidia.com/gpu' exists in 'allocatable' resources
    return int(node_info["status"]["allocatable"].get("nvidia.com/gpu", 0))


def get_used_gpu(node_name):
    """Calculate GPU requests for both active tasks and all tasks on the specified node."""
    result = subprocess.run(
        ["kubectl", "get", "pods", "--all-namespaces", "-o", "json"],
        capture_output=True,
        text=True,
    )
    pods = json.loads(result.stdout)["items"]

    used_gpu_active = 0  # 用于统计排除 Complete 状态的请求 GPU 数量
    used_gpu_all = 0  # 用于统计所有任务（包括 Complete）的请求 GPU 数量
    using_pods = []

    for pod in pods:
        # 确保 pod 中有 "nodeName" 键
        if pod["spec"].get("nodeName") == node_name:
            for container in pod["spec"]["containers"]:
                gpu_request = (
                    container.get("resources", {})
                    .get("requests", {})
                    .get("nvidia.com/gpu")
                )
                if gpu_request:
                    used_gpu_all += int(gpu_request)  # count all requested GPUs
                    # if phase is not "Succeeded", then the task is active
                    if pod["status"]["phase"] == "Running":
                        used_gpu_active += int(gpu_request)
                        using_pods.append(
                            f'{pod["metadata"]["namespace"]}/{pod["metadata"]["name"]} requests GPU: {gpu_request}'
                        )

    return used_gpu_active, used_gpu_all, using_pods


def main(label):
    # label = input("Enter the label to filter nodes (e.g., environment=production): ")

    print("Retrieving GPU resource information for nodes with label:", label)
    print("--------------------------------------------------------")

    nodes = get_nodes_with_label(label)
    available_gpu_nodes_excluding_complete = []
    available_gpu_nodes_including_complete = []

    total_gpu_all = 0

    for node_name, node_ip in nodes:
        total_gpu = get_total_gpu(node_name)
        total_gpu_all += total_gpu
        used_gpu_active, used_gpu_all, using_pods = get_used_gpu(node_name)

        # 分别计算排除和包含 Complete 状态的剩余 GPU 数量
        available_gpu_excluding_complete = total_gpu - used_gpu_active
        available_gpu_including_complete = total_gpu - used_gpu_all

        # Display available GPUs with different colors for emphasis
        print(f"Node: {node_ip} ({node_name})")
        print(
            f"  Available GPUs: {PRIMARY_COLOR}{available_gpu_excluding_complete}{RESET_COLOR}, {SECONDARY_COLOR}{available_gpu_including_complete}{RESET_COLOR} (Include Complete Tasks)"
        )
        print(
            f"  Total: {total_gpu}   Used: {used_gpu_active}    Requested: {used_gpu_all}"
        )

        if using_pods:
            print("  Pods using GPUs (Excluded Complete tasks):")
            for pod in using_pods:
                print(f"    {THIRDARY_COLOR}{pod}{RESET_COLOR}")
        else:
            print("  No Pods are using GPUs.")

        print("--------------------------------------------------------")

        # Append nodes with available GPUs to the respective lists for summary
        if available_gpu_excluding_complete > 0:
            available_gpu_nodes_excluding_complete.append(
                (node_ip, node_name, available_gpu_excluding_complete)
            )
        if available_gpu_including_complete > 0:
            available_gpu_nodes_including_complete.append(
                (node_ip, node_name, available_gpu_including_complete)
            )

    # Output summary of nodes with available GPUs (excluding Complete)
    if available_gpu_nodes_excluding_complete:
        available_gpu_nodes_excluding_complete.sort()
        node_count_excluding = len(available_gpu_nodes_excluding_complete)
        print(
            f"\nNodes with Available GPUs (Excluding Complete): {PRIMARY_COLOR}{node_count_excluding}{RESET_COLOR}"
        )
        print("--------------------------------------------------------")
        for node_ip, node_name, available_gpu in available_gpu_nodes_excluding_complete:
            print(
                f"Node: {THIRDARY_COLOR}{node_ip}{RESET_COLOR} ({node_name}) - Available GPUs (Excluding Complete): {PRIMARY_COLOR}{available_gpu}{RESET_COLOR}"
            )
        print("--------------------------------------------------------")
    else:
        print("\nNo nodes with available GPUs (Excluding Complete).")

    # Output summary of nodes with available GPUs (including Complete)
    if available_gpu_nodes_including_complete:
        available_gpu_nodes_including_complete.sort()
        node_count_including = len(available_gpu_nodes_including_complete)
        print(
            f"\nNodes with Available GPUs (Including Complete): {PRIMARY_COLOR}{node_count_including}{RESET_COLOR}"
        )
        print("--------------------------------------------------------")
        for node_ip, node_name, available_gpu in available_gpu_nodes_including_complete:
            print(
                f"Node: {THIRDARY_COLOR}{node_ip}{RESET_COLOR} ({node_name}) - Available GPUs (Including Complete): {SECONDARY_COLOR}{available_gpu}{RESET_COLOR}"
            )
        print("--------------------------------------------------------")
    else:
        print("\nNo nodes with available GPUs (Including Complete).")

    # Output summary of total GPUs and utilization rate
    total_requested_excluding = sum(
        node[2] for node in available_gpu_nodes_excluding_complete
    )
    total_requested_including = sum(
        total_gpu - available_gpu
        for node_ip, node_name, available_gpu in available_gpu_nodes_including_complete
    )
    print(f"\n{PRIMARY_COLOR}Summary{RESET_COLOR}")
    print("--------------------------------------------------------")
    print(f"Total GPUs across all nodes: {total_gpu_all}")
    print(
        f"Avaliable GPUs across all nodes: {PRIMARY_COLOR}{total_requested_excluding}{RESET_COLOR} (Excluding Complete), {SECONDARY_COLOR}{total_requested_including}{RESET_COLOR}"
    )

    if total_gpu_all == 0:
        utilization_rate = 0
    else:
        utilization_rate = 1 - total_requested_excluding / total_gpu_all

    print(
        f"Utilization rate: {THIRDARY_COLOR}{utilization_rate:.2%}{RESET_COLOR} (Excluding Complete)"
    )

    print("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Retrieve GPU usage information for nodes with a specific label"
    )
    parser.add_argument(
        "label", help="Label to filter nodes, e.g., environment=production"
    )
    args = parser.parse_args()

    main(args.label)
