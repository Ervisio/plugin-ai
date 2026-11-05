/** Files are resolved by the Ervisio bridge inside the signed-in user's private home. */
import { getSdk } from '../sdk';
import { ChatRepository, type Chat, type ChatMeta } from './chatRepository.ts';
export { newChat, titleOf, fit } from './chatRepository.ts';
export type { Chat, ChatMeta } from './chatRepository.ts';

const repository = new ChatRepository({
  read: (path) => getSdk().files.read(path),
  write: (path, data) => getSdk().files.write(path, data),
  list: (path) => getSdk().files.list(path),
  mkdir: (path) => getSdk().files.mkdir(path),
  remove: (path) => getSdk().files.remove(path),
});

export function listChats(): Promise<ChatMeta[]> { return repository.list(); }
export function chatWarnings(): string[] { return [...repository.warnings]; }
export function saveChat(chat: Chat): Promise<void> { return repository.save(chat); }
export function loadChat(id: string): Promise<Chat | undefined> { return repository.load(id); }
export function deleteChat(id: string): Promise<void> { return repository.remove(id); }
export function renameChat(id: string, title: string): Promise<void> { return repository.rename(id, title); }
