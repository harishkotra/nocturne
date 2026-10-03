export type MemoryItem = {
  itemId: string;
  block: string;
  text: string;
  category: string;
  sourceTurn: number | null;
  createdAt: number;
  updatedAt: number;
  ageDays: number;
  active: boolean;
};

export type Block = {
  name: string;
  description: string;
  count: number;
  items: MemoryItem[];
};

export type Snapshot = {
  snapshotId: string;
  seq: number;
  label: string;
  createdAt: number;
  activeCount: number;
};

export type CurvePoint = Snapshot;

export type Message = { turn: number; role: "user" | "assistant"; content: string; createdAt: number };

export type MemoryPayload = {
  blocks: Block[];
  activeCount: number;
  retiredCount: number;
  snapshots: Snapshot[];
  curve: CurvePoint[];
  messages: Message[];
  backend: string;
  blocksExpected: string[];
};

export type Policy = {
  maxActiveFacts: number;
  staleAfterDays: number;
  protectedCategories: string[];
  similarityThreshold?: number;
};

export type Mutation = {
  kind: string;
  itemId: string;
  refs: Record<string, unknown>;
  reason?: string | null;
  rejectionReason?: string | null;
  policy?: string | null;
};

export type PassEvent = {
  pass: string;
  phase: "start" | "done" | "error";
  inputCount: number;
  proposed: number;
  accepted: number;
  rejected: number;
  enforced: number;
  activeCountBefore?: number;
  activeCountAfter?: number;
  beforeSnapshot?: string | null;
  afterSnapshot?: string | null;
  error?: string | null;
  detail?: Record<string, unknown>;
  acceptedMutations?: Mutation[];
  rejectedMutations?: Mutation[];
};

export type SleepResult = {
  dryRun: boolean;
  resultId: string;
  startSnapshot?: string | null;
  policy: Policy;
  startedAt: number;
  finishedAt: number;
  activeCountBefore: number;
  activeCountAfter: number;
  failed: boolean;
  totals: { proposed: number; accepted: number; rejected: number; enforced: number };
  passes: PassEvent[];
  memory?: MemoryPayload;
};

export type ChangedRow = {
  itemId: string;
  before: MemoryItem;
  after: MemoryItem;
  kind: "merged" | "promoted";
  pass?: string | null;
  reason: string;
  fromIds: string[];
  fromBlock?: string | null;
  toBlock: string;
  sources: string[];
};

export type ForgottenRow = {
  itemId: string;
  text: string;
  block: string;
  category: string;
  kind: string;
  pass?: string | null;
  policy?: string | null;
  reason: string;
};

export type RejectedRow = {
  mutationId: number;
  pass: string;
  kind: string;
  itemId: string;
  rejectionReason: string;
  refs: Record<string, unknown>;
};

export type DiffResult = {
  from: string;
  to: string;
  fromMeta?: Snapshot | null;
  toMeta?: Snapshot | null;
  kept: MemoryItem[];
  changed: ChangedRow[];
  forgotten: ForgottenRow[];
  added: MemoryItem[];
  rejected: RejectedRow[];
  counts: { kept: number; changed: number; forgotten: number; added: number; rejected: number };
};

export type ExtractedFact = { itemId?: string; text: string; category: string; sourceTurn: number | null };

export type FactsEvent = {
  proposed: number;
  accepted: ExtractedFact[];
  rejected: { proposal: Record<string, unknown>; rejectionReason: string }[];
  error?: { error: string; status?: number | null; body?: string } | null;
  skipped?: string;
};

export type QuizQuestion = {
  itemId: string;
  question: string;
  expectedAnswer: string;
  modelAnswer: string;
  coverage: number;
  missingTokens: string[];
  correct: boolean;
  attempts: number;
  origin: string;
  leakScore: number;
  block: string;
  category: string;
};

export type ForgottenProbe = {
  itemId: string;
  text: string;
  question: string;
  modelAnswer: string;
  absentFromStore: boolean;
  storeMatches: string[];
  saidAbsent: boolean;
  leakedForgottenContent: boolean;
  answerCoverageOfForgottenFact: number;
  passed: boolean;
};

export type QuizResult = {
  passed: boolean;
  generatedAt: number;
  provider: { baseUrl: string; model: string };
  snapshot?: Snapshot | null;
  policy: Policy;
  survivorsCorrect: number;
  survivorsTotal: number;
  requiredSurvivors: number;
  questions: QuizQuestion[];
  rejectedQuestions: { itemId?: string; question?: string; reason: string }[];
  forgottenQuestion?: ForgottenProbe | null;
  reportPath: string;
};

export type ConnectionTest = {
  baseUrl: string;
  model: string;
  ok: boolean;
  steps: { url: string; status: number | null; body: string }[];
  completion?: string;
  jsonModeAccepted?: boolean | null;
  error?: string;
};

export type Health = {
  ok: boolean;
  backend: string;
  db: string;
  activeCount: number;
  snapshotCount: number;
  provider: { baseUrl: string; model: string; hasKey: boolean };
  presets: Record<string, { label: string; baseUrl: string; model: string; needsKey: string }>;
  defaults: { policy: Policy; settingsKey: string };
};

export type ProviderSettings = {
  preset: string;
  baseUrl: string;
  apiKey: string;
  model: string;
};

export type Settings = {
  version: 1;
  provider: ProviderSettings;
  policy: Policy;
  dryRun: boolean;
};
