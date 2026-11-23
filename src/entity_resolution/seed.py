"""Seed database of known AI organisations (assessment: "mock a small database of 50").

60 entries with canonical name, primary domain, and known aliases (brand
variants, former names, Hugging Face / GitHub handles). Aliases are curated
facts about the organisations, not generated data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SeedEntity:
    canonical: str
    domain: str | None
    aliases: tuple[str, ...] = field(default_factory=tuple)


SEED_ENTITIES: tuple[SeedEntity, ...] = (
    SeedEntity("OpenAI", "openai.com", ("Open AI", "OpenAI Inc", "OpenAI, Inc.", "OpenAI LP", "OpenAI OpCo", "openai")),
    SeedEntity("Anthropic", "anthropic.com", ("Anthropic PBC", "AnthropicAI", "anthropics")),
    SeedEntity("Google DeepMind", "deepmind.google", ("DeepMind", "Deep Mind", "DeepMind Technologies", "google-deepmind")),
    SeedEntity("Google", "google.com", ("Google LLC", "Google AI", "Google Research", "Alphabet")),
    SeedEntity("Meta", "meta.com", ("Meta Platforms", "Meta AI", "Facebook", "Facebook AI Research", "FAIR", "meta-llama", "facebook")),
    SeedEntity("Microsoft", "microsoft.com", ("Microsoft Corporation", "Microsoft Research", "MSFT")),
    SeedEntity("NVIDIA", "nvidia.com", ("Nvidia Corporation", "nvidia")),
    SeedEntity("Mistral AI", "mistral.ai", ("Mistral", "mistralai", "Mistral AI SAS")),
    SeedEntity("Cohere", "cohere.com", ("Cohere Inc", "CohereForAI", "Cohere For AI", "CohereLabs", "Cohere Labs")),
    SeedEntity("Hugging Face", "huggingface.co", ("HuggingFace", "huggingface", "Hugging Face Inc")),
    SeedEntity("Stability AI", "stability.ai", ("StabilityAI", "stabilityai", "Stability")),
    SeedEntity("xAI", "x.ai", ("X.AI", "X.AI Corp", "xai-org")),
    SeedEntity("Perplexity", "perplexity.ai", ("Perplexity AI", "perplexity-ai")),
    SeedEntity("Scale AI", "scale.com", ("Scale", "ScaleAI", "scaleai")),
    SeedEntity("Databricks", "databricks.com", ("Databricks Inc", "databricks", "MosaicML")),
    SeedEntity("Inflection AI", "inflection.ai", ("Inflection",)),
    SeedEntity("Character.AI", "character.ai", ("Character AI", "Character Technologies")),
    SeedEntity("Runway", "runwayml.com", ("RunwayML", "Runway AI", "runwayml")),
    SeedEntity("Midjourney", "midjourney.com", ("Midjourney Inc",)),
    SeedEntity("Adept", "adept.ai", ("Adept AI",)),
    SeedEntity("AI21 Labs", "ai21.com", ("AI21", "ai21labs")),
    SeedEntity("Aleph Alpha", "aleph-alpha.com", ("Aleph-Alpha", "aleph alpha gmbh")),
    SeedEntity("Together AI", "together.ai", ("Together", "togethercomputer", "Together Computer")),
    SeedEntity("Groq", "groq.com", ("Groq Inc",)),
    SeedEntity("Cerebras", "cerebras.ai", ("Cerebras Systems",)),
    SeedEntity("SambaNova", "sambanova.ai", ("SambaNova Systems",)),
    SeedEntity("DeepSeek", "deepseek.com", ("deepseek-ai", "DeepSeek AI", "Hangzhou DeepSeek")),
    SeedEntity("Alibaba Qwen", "qwenlm.github.io", ("Qwen", "Qwen Team", "Alibaba Cloud Qwen")),
    SeedEntity("Moonshot AI", "moonshot.ai", ("moonshotai", "Kimi")),
    SeedEntity("Zhipu AI", "zhipuai.cn", ("THUDM", "zai-org", "Z.ai", "Zhipu")),
    SeedEntity("01.AI", "01.ai", ("01-ai", "Zero One AI", "Lingyiwanwu")),
    SeedEntity("Baidu", "baidu.com", ("Baidu Inc", "PaddlePaddle")),
    SeedEntity("Tencent", "tencent.com", ("Tencent Hunyuan", "tencent")),
    SeedEntity("ByteDance", "bytedance.com", ("ByteDance Seed", "ByteDance-Seed")),
    SeedEntity("Apple", "apple.com", ("Apple Inc", "apple")),
    SeedEntity("Amazon", "amazon.com", ("Amazon Web Services", "AWS", "Amazon Science", "amazon")),
    SeedEntity("IBM", "ibm.com", ("International Business Machines", "IBM Research", "ibm-granite")),
    SeedEntity("Salesforce", "salesforce.com", ("Salesforce AI Research", "Salesforce Inc")),
    SeedEntity("Allen Institute for AI", "allenai.org", ("AI2", "Ai2", "allenai")),
    SeedEntity("EleutherAI", "eleuther.ai", ("Eleuther AI", "eleutherai")),
    SeedEntity("LAION", "laion.ai", ("laion",)),
    SeedEntity("Replicate", "replicate.com", ("Replicate Inc",)),
    SeedEntity("Pinecone", "pinecone.io", ("Pinecone Systems",)),
    SeedEntity("Weaviate", "weaviate.io", ("SeMI Technologies",)),
    SeedEntity("Qdrant", "qdrant.tech", ("Qdrant Solutions",)),
    SeedEntity("LangChain", "langchain.com", ("LangChain Inc", "langchain-ai")),
    SeedEntity("LlamaIndex", "llamaindex.ai", ("Llama Index", "run-llama")),
    SeedEntity("Weights & Biases", "wandb.ai", ("Weights and Biases", "WandB", "W&B", "wandb")),
    SeedEntity("Anyscale", "anyscale.com", ("Anyscale Inc",)),
    SeedEntity("Modal", "modal.com", ("Modal Labs",)),
    SeedEntity("Fireworks AI", "fireworks.ai", ("Fireworks",)),
    SeedEntity("ElevenLabs", "elevenlabs.io", ("Eleven Labs", "elevenlabs")),
    SeedEntity("Synthesia", "synthesia.io", ("Synthesia Ltd",)),
    SeedEntity("Jasper", "jasper.ai", ("Jasper AI",)),
    SeedEntity("Writer", "writer.com", ("Writer Inc", "Writer.com")),
    SeedEntity("Glean", "glean.com", ("Glean Technologies",)),
    SeedEntity("Harvey", "harvey.ai", ("Harvey AI", "Counsel AI")),
    SeedEntity("Cursor", "cursor.com", ("Anysphere", "Anysphere Inc", "Cursor AI")),
    SeedEntity("Reka AI", "reka.ai", ("Reka",)),
    SeedEntity("Liquid AI", "liquid.ai", ("LiquidAI",)),
    SeedEntity("Sakana AI", "sakana.ai", ("SakanaAI",)),
    SeedEntity("Nous Research", "nousresearch.com", ("NousResearch",)),
)
