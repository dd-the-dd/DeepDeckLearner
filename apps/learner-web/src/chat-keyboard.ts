import type { KeyboardEvent } from 'react';

/** Keep multiline/IME input native; send a chat only on a fresh Enter press. */
export function submitChatOnEnter(event: KeyboardEvent<HTMLTextAreaElement>, canSend: boolean) {
  if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey
      || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;

  event.preventDefault();
  if (canSend && !event.repeat) event.currentTarget.form?.requestSubmit();
}
