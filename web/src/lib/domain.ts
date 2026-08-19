export type MediaKind = "image" | "music" | "video";
export type MessageRole = "user" | "assistant";
export type MessageStatus = "planning" | "running" | "complete" | "failed";

export interface TextPart {
  type: "text";
  text: string;
}

export interface ImagePart {
  type: "image_url";
  image_url: { url: string };
}

export type MessagePart = TextPart | ImagePart;
export type MessageContent = string | MessagePart[];

export interface ChatMessage {
  role: MessageRole;
  content: MessageContent;
  requestId?: string;
  tasks?: string[];
  status?: MessageStatus;
  kind?: MediaKind;
  mode?: string;
  prompt?: string;
  payload?: Record<string, unknown>;
  jobId?: string;
  url?: string;
  error?: string;
  startedAt?: number;
  elapsed?: number | string;
  batchId?: string;
  position?: number;
  qualityScore?: number;
}

export interface AgentMessage extends ChatMessage {
  role: "assistant";
  requestId: string;
  status: MessageStatus;
}

export function isPlanningAgentMessage(message: ChatMessage): message is AgentMessage {
  return message.role === "assistant"
    && message.status === "planning"
    && typeof message.requestId === "string";
}

export interface Chat {
  id: string;
  title: string;
  messages: ChatMessage[];
}

export function messageText(content: MessageContent): string {
  if (typeof content === "string") return content;
  return content.find((part): part is TextPart => part.type === "text")?.text ?? "";
}

export function messageImages(content: MessageContent): string[] {
  if (typeof content === "string") return [];
  return content
    .filter((part): part is ImagePart => part.type === "image_url")
    .map(part => part.image_url.url);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
