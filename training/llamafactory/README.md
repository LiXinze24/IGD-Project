# LLaMA-Factory fine-tuning

[qwen2_5coder_lora_s1_sft.yaml](qwen2_5coder_lora_s1_sft.yaml) configures supervised fine-tuning of [Qwen2.5-Coder-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct) with LoRA in [LLaMA-Factory](https://github.com/hiyouga/LlamaFactory). It continues training an existing LoRA adapter.

| Setting | Value |
| --- | --- |
| Base model | Qwen2.5-Coder-7B-Instruct |
| LoRA rank / alpha / dropout | 32 / 64 / 0.05 |
| Target modules | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Dataset name | deepcad_cq_train |
| Prompt template | alpaca |
| Sequence cutoff / sample limit | 1,536 tokens / 80,000 examples |
| Per-device batch / gradient accumulation | 1 / 16 |
| Learning rate / scheduler | 1e-5 / cosine |
| Training epochs / warmup ratio | 0.5 / 0.03 |
| Precision | BF16 |

## Prepare the environment and inputs

Install LLaMA-Factory in a training environment following its [installation guide](https://llamafactory.readthedocs.io/en/latest/getting_started/installation.html). Select a PyTorch/CUDA build for your hardware and use a GPU with BF16 support. The IGD coordination runtime and its optional CAD dependency do not install the training stack.

Run the commands below from the IGD repository root. Relative paths in the configuration are resolved from that directory.

1. Make the base model available through its Hugging Face identifier, or set `model_name_or_path` to a local model directory.
2. Place the existing LoRA adapter at `checkpoints/qwen2_5coder_lora_s1/checkpoint-4536/`, or set `adapter_name_or_path` to its location. The directory must contain the adapter configuration and weights, not just a base model.
3. Place your dataset at `data/deepcad_cq_train.json`. Copy [dataset_info.example.json](dataset_info.example.json) to `data/dataset_info.json` and adjust its filename and columns to match your data.
4. Set `output_dir` to the directory for this training run. The supplied configuration enables output-directory overwrite.

The registration example maps Alpaca-style records with `instruction`, `input` and `output` fields; it is a mapping example, not a dataset. Use the [LLaMA-Factory data preparation guide](https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html) if your records use another structure. Dataset formatting and the model's prompt `template` are separate settings. This recipe uses `template: alpaca`; select the matching template for any subsequent inference.

The configuration and registration example are included here. Training data, base-model weights and adapter checkpoints are supplied separately.

## Train

```console
llamafactory-cli train training/llamafactory/qwen2_5coder_lora_s1_sft.yaml
```

LLaMA-Factory accepts the YAML path through its [SFT training command](https://llamafactory.readthedocs.io/en/latest/getting_started/sft.html). With the existing adapter supplied, this recipe continues optimizing that adapter. `resume_from_checkpoint: null` starts a new trainer run; it does not restore an earlier optimizer or scheduler state. See the [adapter loading implementation](https://github.com/hiyouga/LlamaFactory/blob/main/src/llamafactory/model/adapter.py) for the distinction.

For a new LoRA run from the base model, remove `adapter_name_or_path` in your working configuration and choose a new output directory. Training outputs default to `outputs/training/qwen2_5coder_lora_s1_refine/`.
