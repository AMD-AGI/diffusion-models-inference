# xDiT diffusion model inference

xDiT is a high performance distributed inference framework focusing on diffusion models.

xDiT has a CLI which can be used to conveniently run many contemporary models. For example, to run Wan 2.2 image-to-video generation model, simply execute

```
xdit    --model "Wan-AI/Wan2.2-I2V-A14B-Diffusers" \
        --prompt "Summer beach vacation style, a white cat wearing sunglasses sits on a surfboard. The fluffy-furred feline gazes directly at the camera with a relaxed expression. Blurred beach scenery forms the background featuring crystal-clear waters, distant green hills, and a blue sky dotted with white clouds. The cat assumes a naturally relaxed posture, as if savoring the sea breeze and warm sunlight. A close-up shot highlights the feline's intricate details and the refreshing atmosphere of the seaside." \
        --height 720 \
        --width 1280 \
        --input_images "/app/data/wan_input.jpg"
        --num_frames 81 \
        --ulysses_degree 8 \
        --seed 42 \
        --num_repetitions 1 \
        --num_inference_steps 40 \
        --use_torch_compile
```

anywhere inside the image. For a list of supported models and further instructions, see `/app/xDiT/README.md`.