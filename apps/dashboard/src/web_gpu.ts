/**
 * GPU-accelerated compositing of privacy masks onto a screenshot preview.
 *
 * This mirrors what the manual mask overlay in VisualApproval.tsx does with
 * CSS boxes, but actually burns the opaque mask rectangles into the pixels
 * using a WebGPU render pipeline, entirely on the client, before anything is
 * sent to the backend for approval. Not wired into any component yet.
 *
 * Usage (once wired in):
 *   const gpu = await createWebGpuRedactor();
 *   if (gpu) {
 *     const blob = await gpu.applyMasks(imageBitmap, masks);
 *   } else {
 *     // fall back to the existing CSS-overlay / server-side masking path
 *   }
 */

export interface MaskRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WebGpuRedactor {
  /** Burns opaque black rectangles over `masks` into `image`, GPU-side. */
  applyMasks(image: ImageBitmap, masks: MaskRect[]): Promise<Blob>;
  /** Releases the GPU device. Safe to call multiple times. */
  destroy(): void;
}

const SHADER_SOURCE = /* wgsl */ `
  struct MaskRect {
    x: f32,
    y: f32,
    width: f32,
    height: f32,
  };

  struct Masks {
    count: u32,
    rects: array<MaskRect>,
  };

  @group(0) @binding(0) var sourceTex: texture_2d<f32>;
  @group(0) @binding(1) var sourceSampler: sampler;
  @group(0) @binding(2) var<storage, read> masks: Masks;

  struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
  };

  @vertex
  fn vs_main(@builtin(vertex_index) vertexIndex: u32) -> VertexOut {
    var positions = array<vec2<f32>, 6>(
      vec2<f32>(-1.0, -1.0), vec2<f32>(1.0, -1.0), vec2<f32>(-1.0, 1.0),
      vec2<f32>(-1.0, 1.0), vec2<f32>(1.0, -1.0), vec2<f32>(1.0, 1.0),
    );
    var uvs = array<vec2<f32>, 6>(
      vec2<f32>(0.0, 1.0), vec2<f32>(1.0, 1.0), vec2<f32>(0.0, 0.0),
      vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 1.0), vec2<f32>(1.0, 0.0),
    );
    var out: VertexOut;
    out.position = vec4<f32>(positions[vertexIndex], 0.0, 1.0);
    out.uv = uvs[vertexIndex];
    return out;
  }

  @fragment
  fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let dims = vec2<f32>(textureDimensions(sourceTex));
    let pixel = in.uv * dims;
    for (var i: u32 = 0u; i < masks.count; i = i + 1u) {
      let rect = masks.rects[i];
      if (pixel.x >= rect.x && pixel.x < rect.x + rect.width &&
          pixel.y >= rect.y && pixel.y < rect.y + rect.height) {
        return vec4<f32>(0.0, 0.0, 0.0, 1.0);
      }
    }
    return textureSample(sourceTex, sourceSampler, in.uv);
  }
`;

const MAX_MASKS = 64;

/**
 * Detects WebGPU support and prepares a reusable device/pipeline.
 * Returns null when WebGPU is unavailable so callers can fall back cleanly.
 */
export async function createWebGpuRedactor(): Promise<WebGpuRedactor | null> {
  const gpu = (navigator as Navigator & { gpu?: GPU }).gpu;
  if (!gpu) return null;

  const adapter = await gpu.requestAdapter();
  if (!adapter) return null;
  const device = await adapter.requestDevice();

  const shaderModule = device.createShaderModule({ code: SHADER_SOURCE });
  const pipeline = device.createRenderPipeline({
    layout: "auto",
    vertex: { module: shaderModule, entryPoint: "vs_main" },
    fragment: {
      module: shaderModule,
      entryPoint: "fs_main",
      targets: [{ format: "rgba8unorm" }],
    },
    primitive: { topology: "triangle-list" },
  });

  const sampler = device.createSampler({ magFilter: "linear", minFilter: "linear" });

  function packMasks(masks: MaskRect[]): Float32Array {
    const clipped = masks.slice(0, MAX_MASKS);
    // count (padded to 16 bytes) + up to MAX_MASKS * 4 floats
    const buffer = new Float32Array(4 + clipped.length * 4);
    buffer[0] = clipped.length;
    clipped.forEach((rect, i) => {
      buffer[4 + i * 4 + 0] = rect.x;
      buffer[4 + i * 4 + 1] = rect.y;
      buffer[4 + i * 4 + 2] = rect.width;
      buffer[4 + i * 4 + 3] = rect.height;
    });
    return buffer;
  }

  async function applyMasks(image: ImageBitmap, masks: MaskRect[]): Promise<Blob> {
    const { width, height } = image;

    const texture = device.createTexture({
      size: [width, height],
      format: "rgba8unorm",
      usage:
        GPUTextureUsage.TEXTURE_BINDING |
        GPUTextureUsage.COPY_DST |
        GPUTextureUsage.RENDER_ATTACHMENT,
    });
    device.queue.copyExternalImageToTexture({ source: image }, { texture }, [width, height]);

    const packed = packMasks(masks);
    const maskBuffer = device.createBuffer({
      size: Math.max(packed.byteLength, 16),
      usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
    });
    device.queue.writeBuffer(maskBuffer, 0, packed);

    const outputTexture = device.createTexture({
      size: [width, height],
      format: "rgba8unorm",
      usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC,
    });

    const bindGroup = device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [
        { binding: 0, resource: texture.createView() },
        { binding: 1, resource: sampler },
        { binding: 2, resource: { buffer: maskBuffer } },
      ],
    });

    const encoder = device.createCommandEncoder();
    const pass = encoder.beginRenderPass({
      colorAttachments: [
        {
          view: outputTexture.createView(),
          loadOp: "clear",
          storeOp: "store",
          clearValue: { r: 0, g: 0, b: 0, a: 1 },
        },
      ],
    });
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.draw(6);
    pass.end();

    const bytesPerRow = Math.ceil((width * 4) / 256) * 256;
    const readBuffer = device.createBuffer({
      size: bytesPerRow * height,
      usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
    });
    encoder.copyTextureToBuffer(
      { texture: outputTexture },
      { buffer: readBuffer, bytesPerRow },
      [width, height],
    );
    device.queue.submit([encoder.finish()]);

    await readBuffer.mapAsync(GPUMapMode.READ);
    const mapped = new Uint8Array(readBuffer.getMappedRange().slice(0));
    readBuffer.unmap();

    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D context unavailable for WebGPU readback");
    const imageData = ctx.createImageData(width, height);
    for (let y = 0; y < height; y++) {
      const srcStart = y * bytesPerRow;
      const dstStart = y * width * 4;
      imageData.data.set(mapped.subarray(srcStart, srcStart + width * 4), dstStart);
    }
    ctx.putImageData(imageData, 0, 0);

    texture.destroy();
    outputTexture.destroy();
    maskBuffer.destroy();
    readBuffer.destroy();

    return canvas.convertToBlob({ type: "image/png" });
  }

  function destroy() {
    device.destroy();
  }

  return { applyMasks, destroy };
}