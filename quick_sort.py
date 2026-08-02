"""快速排序算法实现（Quick Sort）

核心思想：分治法（Divide and Conquer）
1. 从数组中选取一个"基准"（pivot）元素
2. 将数组分区：小于基准的元素移到左边，大于基准的移到右边
3. 递归地对左右两个子数组重复上述过程

平均时间复杂度: O(n log n)
最坏时间复杂度: O(n^2)（当基准总是选到最大/最小元素时）
空间复杂度: O(log n)（递归调用栈）
"""


def quick_sort(arr):
    """快速排序（返回新列表，不修改原数组）

    使用列表推导式的简洁写法，易于理解但会额外分配内存。
    """
    if len(arr) <= 1:
        return arr

    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]

    return quick_sort(left) + middle + quick_sort(right)


def quick_sort_inplace(arr, low=0, high=None):
    """快速排序（原地排序，直接修改传入的列表，更省内存）"""
    if high is None:
        high = len(arr) - 1

    if low < high:
        # partition 返回基准元素的最终位置
        pi = partition(arr, low, high)
        # 递归排序基准左右两侧
        quick_sort_inplace(arr, low, pi - 1)
        quick_sort_inplace(arr, pi + 1, high)

    return arr


def partition(arr, low, high):
    """Lomuto 分区方案：以最后一个元素为基准"""
    pivot = arr[high]
    i = low - 1  # i 指向小于基准区域的最后一个元素

    for j in range(low, high):
        if arr[j] < pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]

    # 把基准放到正确位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


if __name__ == "__main__":
    # 测试用例
    test_cases = [
        [3, 6, 8, 10, 1, 2, 1],
        [5, 4, 3, 2, 1],
        [1, 2, 3, 4, 5],
        [42],
        [],
        [2, 2, 2, 2],
    ]

    for case in test_cases:
        expected = sorted(case)

        # 非原地版本
        result1 = quick_sort(case)
        assert result1 == expected, f"quick_sort 失败: {case}"

        # 原地版本
        arr_copy = case.copy()
        quick_sort_inplace(arr_copy)
        assert arr_copy == expected, f"quick_sort_inplace 失败: {case}"

        print(f"原数组: {case}")
        print(f"排序后: {result1}\n")

    print("✅ 所有测试通过！")
