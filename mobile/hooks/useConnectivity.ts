/**
 * Detects network connectivity using the NetInfo API bundled with Expo.
 * Returns isConnected (boolean) and isInternetReachable (boolean | null).
 */

import { useState, useEffect } from 'react';
import NetInfo, { NetInfoState } from '@react-native-community/netinfo';

interface ConnectivityState {
  isConnected: boolean;
  isInternetReachable: boolean | null;
}

export function useConnectivity(): ConnectivityState {
  const [state, setState] = useState<ConnectivityState>({
    isConnected: true,         // optimistic default
    isInternetReachable: null,
  });

  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener((netState: NetInfoState) => {
      setState({
        isConnected: netState.isConnected ?? false,
        isInternetReachable: netState.isInternetReachable,
      });
    });
    return unsubscribe;
  }, []);

  return state;
}
