// 小规模 CUDA 向量加法证据；不处理用户数据，不是模型推理。
#include <cuda_runtime.h>
#include <cmath>
#include <cstdio>
#include <vector>

__global__ void add_vectors(const float* left, const float* right, float* output, int count) {
    const int index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index < count) output[index] = left[index] + right[index];
}

int main() {
    constexpr int count = 1 << 20;
    constexpr size_t bytes = static_cast<size_t>(count) * sizeof(float);
    cudaDeviceProp device{};
    if (cudaGetDeviceProperties(&device, 0) != cudaSuccess) return 2;

    std::vector<float> left(count), right(count), output(count);
    for (int i = 0; i < count; ++i) {
        left[i] = static_cast<float>(i % 17);
        right[i] = static_cast<float>((i % 13) * 2);
    }
    float *gpu_left = nullptr, *gpu_right = nullptr, *gpu_output = nullptr;
    if (cudaMalloc(&gpu_left, bytes) != cudaSuccess ||
        cudaMalloc(&gpu_right, bytes) != cudaSuccess ||
        cudaMalloc(&gpu_output, bytes) != cudaSuccess) return 3;
    if (cudaMemcpy(gpu_left, left.data(), bytes, cudaMemcpyHostToDevice) != cudaSuccess ||
        cudaMemcpy(gpu_right, right.data(), bytes, cudaMemcpyHostToDevice) != cudaSuccess) return 4;

    cudaEvent_t start{}, stop{};
    if (cudaEventCreate(&start) != cudaSuccess || cudaEventCreate(&stop) != cudaSuccess) return 5;
    cudaEventRecord(start);
    add_vectors<<<(count + 255) / 256, 256>>>(gpu_left, gpu_right, gpu_output, count);
    cudaEventRecord(stop);
    if (cudaGetLastError() != cudaSuccess || cudaEventSynchronize(stop) != cudaSuccess) return 6;
    float elapsed_ms = 0.0f;
    cudaEventElapsedTime(&elapsed_ms, start, stop);
    if (cudaMemcpy(output.data(), gpu_output, bytes, cudaMemcpyDeviceToHost) != cudaSuccess) return 7;

    double checksum = 0.0;
    for (int i = 0; i < count; ++i) {
        const float expected = left[i] + right[i];
        if (std::fabs(output[i] - expected) > 0.001f) return 8;
        checksum += output[i];
    }
    std::printf("{\"ok\":true,\"kind\":\"CUDA vector addition; not model inference\","
                "\"device\":\"%s\",\"compute_capability\":\"%d.%d\","
                "\"elements\":%d,\"elapsed_ms\":%.3f,\"result_checksum\":%.0f}\n",
                device.name, device.major, device.minor, count, elapsed_ms, checksum);
    cudaEventDestroy(start);
    cudaEventDestroy(stop);
    cudaFree(gpu_left);
    cudaFree(gpu_right);
    cudaFree(gpu_output);
    return 0;
}
