export const getItem = (key: string): Promise<string | null> =>
  Promise.resolve(localStorage.getItem(key));

export const setItem = (key: string, value: string): Promise<void> =>
  Promise.resolve(void localStorage.setItem(key, value));

export const deleteItem = (key: string): Promise<void> =>
  Promise.resolve(void localStorage.removeItem(key));
