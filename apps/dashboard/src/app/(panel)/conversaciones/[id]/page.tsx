import { ConversationDetail } from "@/components/conversations/conversation-detail";

export default async function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // Keyed by chat: moving to another one never carries over a draft or a transcript.
  return (
    <div className="mx-auto max-w-3xl">
      <ConversationDetail key={id} id={id} />
    </div>
  );
}
