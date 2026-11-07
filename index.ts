import { generateText } from "ai";

// A plain "provider/model" string routes through Vercel AI Gateway,
// authenticated by AI_GATEWAY_API_KEY (loaded from .env.local by `npm run ai:example`).
const { text } = await generateText({
  model: "moonshotai/kimi-k3",
  prompt: "Invent a new holiday and describe its traditions.",
});

console.log(text);
