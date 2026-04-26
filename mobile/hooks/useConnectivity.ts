import { useState, useEffect } from 'react';
import { Platform } from 'react-native';
import NetInfo, { NetInfoState } from '@react-native-community/netinfo';

interface ConnectivityState {
  isConnected: boolean;
  isInternetReachable: boolean | null;
}

export function useConnectivity(): ConnectivityState {
  const [state, setState] = useState<ConnectivityState>({
    isConnected: true,
    isInternetReachable: null,
  });

  useEffect(() => {
    if (Platform.OS === 'web') return;
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
