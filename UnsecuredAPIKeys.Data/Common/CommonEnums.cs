namespace UnsecuredAPIKeys.Data.Common
{
    public enum SearchProviderEnum
    {
        Unknown = -99,
        GitHub = 1,
        GitLab = 2,
        BitBucket = 3,
        SourceGraph = 4
    }

    public enum ApiStatusEnum
    {
        // The key was found but not yet checked for validity  
        Unverified = -99,

        // The key was checked and is valid/working  
        Valid = 1,

        // The key was checked and is not working (invalid, expired, revoked, etc.)  
        Invalid = 0,

        // The key was removed at the request of the repo owner or by admin action  
        Removed = 3,

        // The key was flagged for removal at the request of the repo owner.
        FlaggedForRemoval = 4,

        // The key is no longer valid. (Fixed / Removed)
        NoLongerWorking = 5,

        // The key was checked and is erroring out for some reason.
        Error = 6,

        // The key is valid but has no credits/quota
        ValidNoCredits = 7
    }

    public enum ApiTypeEnum
    {
        // Default
        Unknown = -99,

        // AI Services (100+)
        OpenAI = 100,
        AzureOpenAI = 110,        // Not used | Requires too much info (base org URL + api key)
        AnthropicClaude = 120,    // More specific than AnthropicAI
        GoogleAI = 130,
        Cohere = 140,
        HuggingFace = 150,
        StabilityAI = 160,        // Stability.ai
        MistralAI = 170,
        Replicate = 180,
        TogetherAI = 190,
        OpenRouter = 195,
        
        // New AI Services
        PerplexityAI = 196,
        Groq = 197,
        DeepSeek = 198,
        ElevenLabs = 199,
        RunwayML = 201,
        AssemblyAI = 202,
        Pinecone = 203,
        Weaviate = 204,
        ChromaDB = 205,
        LangChain = 206,
        MoonshotAI = 207,

        // Cloud Providers (200+)
        AWS = 200,                // AWS Access Keys
        Azure = 210,              // Azure Service Principal Keys
        GCP = 220,                // Google Cloud Platform Keys

        // Source Control (300+)
        GitHub = 300,             // GitHub Personal Access Tokens
        GitLab = 310,
        BitBucket = 320,

        // Common Services (400+)
        Stripe = 400,             // Payment processing
        SendGrid = 410,           // Email service
        Twilio = 420,             // SMS/Voice services
        MongoDB = 430,            // Database access keys
        Firebase = 440            // Google Firebase keys
    }

    public enum IssueVerificationStatus
    {
        NotFound = 0,
        Open = 1,
        Closed = 2,
        VerificationError = 3
    }
}