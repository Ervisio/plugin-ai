import { getSdk } from '../sdk';

/**
 * `files.write` creates the plugin's declared folder, but not folders inside it. Write, and when the folder is
 * missing make it (and its parents) and write again.
 */
export async function writeEnsured(path: string, data: string): Promise<void> {
  const sdk = getSdk();
  try {
    await sdk.files.write(path, data);
  } catch (e) {
    if ((e as { code?: string })?.code !== 'not_found') throw e;
    const dir = path.slice(0, path.lastIndexOf('/'));
    await sdk.files.mkdir(dir);
    await sdk.files.write(path, data);
  }
}
