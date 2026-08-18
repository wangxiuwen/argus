# Third-party notices

Mira embeds the native `vpipe` runtime from
[VPIPE v0.1.26](https://github.com/tgo-app-dev/vpipe/releases/tag/v0.1.26),
licensed under Apache-2.0. The application bundle includes VPIPE's license,
notice, and the license texts for its bundled dynamic libraries under
`Contents/Resources/Licenses/`. A source-distribution copy of the VPIPE license
is also available at [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).

The application does not redistribute MiniMax H3 model weights. If the user
accepts the model license, VPIPE downloads the weights directly from
[`Comfy-Org/MiniMax-H3`](https://huggingface.co/Comfy-Org/MiniMax-H3), whose
model card points to the
[`MiniMax H3 Community License Agreement`](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE).
That license, including its territory and acceptable-use restrictions, governs
the downloaded weights and their use independently of Mira's MIT license.

The optional Turbo LoRA is downloaded directly from
[`larryvrh/MiniMax-H3-Turbo-Lora`](https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora)
and is marked Apache-2.0 by its model card.

Mira also embeds the MIT-licensed
[`mlx-serve`](https://github.com/ddalcu/mlx-serve) v26.8.8 runtime. Its unmodified
`LICENSE`, `NOTICE`, `LICENSE-APACHE-2.0`, and dependent dynamic libraries are
included beside the binary in `Mira.app/Contents/Helpers/mlx-serve/`.

Mira does not redistribute the image or music model weights. On first use,
mlx-serve downloads `Runpod/FLUX.2-klein-4B-mflux-4bit` for images or
`ddalcu/MiniMax-Music3-MLX-Serve-8bit` for music. The license published with each
model applies independently to those downloaded weights.
