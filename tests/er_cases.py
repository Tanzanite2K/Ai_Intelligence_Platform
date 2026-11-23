"""Labelled entity-resolution evaluation set (hand-written test cases).

(raw name, website or None, expected canonical or None)
``None`` expected == must NOT be merged into any seed entity (negative case).
"""

POSITIVE = [
    ("OpenAI", None, "OpenAI"), ("Open AI", None, "OpenAI"), ("OpenAI Inc.", None, "OpenAI"), ("OpenAI, Inc.", None, "OpenAI"),
    ("OPENAI", None, "OpenAI"), ("openai llc", None, "OpenAI"), ("Ｏｐｅｎ ＡＩ", None, "OpenAI"), ("Open-AI", None, "OpenAI"),
    ("OpenAI LP", None, "OpenAI"), (" openai ", None, "OpenAI"), ("OpenAl", None, None),  # l/I confusion is NOT auto-merged (see below)
    ("Anthropic PBC", None, "Anthropic"), ("ANTHROPIC", None, "Anthropic"), ("Anthropic, PBC", None, "Anthropic"),
    ("DeepMind", None, "Google DeepMind"), ("Deep Mind", None, "Google DeepMind"), ("Google DeepMind", None, "Google DeepMind"),
    ("Mistral AI SAS", None, "Mistral AI"), ("mistralai", None, "Mistral AI"), ("Mistral", None, "Mistral AI"),
    ("Hugging Face, Inc.", None, "Hugging Face"), ("HuggingFace", None, "Hugging Face"), ("huggingface", None, "Hugging Face"),
    ("Nvidia Corp.", None, "NVIDIA"), ("NVIDIA Corporation", None, "NVIDIA"), ("nVidia", None, "NVIDIA"),
    ("Meta Platforms, Inc.", None, "Meta"), ("meta-llama", None, "Meta"), ("Facebook AI Research", None, "Meta"),
    ("Microsoft Corporation", None, "Microsoft"), ("Stability AI Ltd", None, "Stability AI"), ("stabilityai", None, "Stability AI"),
    ("Cohere Inc.", None, "Cohere"), ("CohereForAI", None, "Cohere"), ("Perplexity AI, Inc.", None, "Perplexity"),
    ("Scale AI, Inc.", None, "Scale AI"), ("Databricks Inc.", None, "Databricks"), ("xAI Corp", None, "xAI"), ("X.AI", None, "xAI"),
    ("Runway ML", None, "Runway"), ("RunwayML", None, "Runway"), ("Character AI", None, "Character.AI"), ("Character.AI", None, "Character.AI"),
    ("AI21", None, "AI21 Labs"), ("Together Computer", None, "Together AI"), ("deepseek-ai", None, "DeepSeek"), ("DeepSeek AI Co., Ltd.", None, "DeepSeek"),
    ("Weights and Biases", None, "Weights & Biases"), ("W&B", None, "Weights & Biases"), ("wandb", None, "Weights & Biases"),
    ("Eleven Labs Inc", None, "ElevenLabs"), ("ElevenLabs", None, "ElevenLabs"), ("Anysphere Inc.", None, "Cursor"),
    ("LangChain, Inc.", None, "LangChain"), ("Llama Index", None, "LlamaIndex"), ("Allen Institute for AI", None, "Allen Institute for AI"),
    ("AI2", None, "Allen Institute for AI"), ("Groq, Inc.", None, "Groq"), ("Cerebras Systems Inc.", None, "Cerebras"),
    ("Liquid AI, Inc.", None, "Liquid AI"), ("Sakana AI", None, "Sakana AI"), ("NousResearch", None, "Nous Research"),
    ("Acme Corp", "https://www.openai.com/blog", "OpenAI"),  # domain evidence
    ("Moonshot AI", None, "Moonshot AI"), ("Qwen Team", None, "Alibaba Qwen"), ("THUDM", None, "Zhipu AI"),
    ("Anthropic Inc", None, "Anthropic"), ("Glean Technologies, Inc.", None, "Glean"), ("Synthesia Ltd.", None, "Synthesia"),
]

NEGATIVE = [  # similar strings, different organisations -> must not merge into the seed entity
    ("Mistral Health", None), ("Cohere Health", None), ("Scale Computing", None), ("OpenAir Systems", None),
    ("Anthropos Labs", None), ("Meta Materials", None), ("Groqqy Analytics", None), ("Runway Financial", None),
    ("Writer", "https://writerapp.io"), ("Glean Health", None), ("Modal Commerce", None), ("Pinecone Research", None),
    ("Replicate Health", None), ("Harvey Nash", None), ("Cursor Insight", None), ("Jasper Ridge", None),
    ("Deep Minds Academy", None), ("Stability Insurance", None), ("Together Health", None), ("Liquid Death", None),
    ("OpenAl", None),
    ("Mistrall AI", None),  # single-word brand typo -> human review, never an automatic merge
]

# Real false merges found in the first live dataset (2026-11-21): (first-seen org, later org) must stay separate.
LIVE_REGRESSIONS = [("Contour", "Conntour"), ("DataSuite", "Datasite"), ("Candor", "Candoor")]
