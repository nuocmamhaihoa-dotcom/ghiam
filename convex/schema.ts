/**
 * Convex schema scaffold for production sync.
 * Run: npx convex dev (with CONVEX_AGENT_MODE=anonymous for cloud agents)
 */
import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  calls: defineTable({
    title: v.string(),
    industry: v.string(),
    product: v.string(),
    agentName: v.string(),
    outcome: v.union(
      v.literal("won"),
      v.literal("lost"),
      v.literal("callback"),
      v.literal("unknown"),
    ),
    durationSec: v.number(),
    transcript: v.string(),
    createdAt: v.number(),
  })
    .index("by_industry", ["industry"])
    .index("by_outcome", ["outcome"])
    .index("by_created", ["createdAt"]),

  analyses: defineTable({
    callId: v.id("calls"),
    openingScore: v.number(),
    closingScore: v.number(),
    overallScore: v.number(),
    speakingRateWpm: v.number(),
    toneLabel: v.string(),
    keywordsHit: v.array(v.string()),
    coachingTips: v.array(v.string()),
    payload: v.any(),
    createdAt: v.number(),
  }).index("by_call", ["callId"]),
});
