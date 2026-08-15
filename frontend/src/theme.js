import { createTheme } from '@mui/material/styles';

const theme = createTheme({
  palette: {
    mode: 'light',
    background: {
      default: '#f4f3ee',
      paper: '#fffefa',
    },
    primary: {
      main: '#366b4a',
      dark: '#255137',
    },
    error: {
      main: '#9d4134',
    },
    text: {
      primary: '#20231f',
      secondary: '#686e65',
    },
    divider: '#dfe1da',
  },
  typography: {
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    overline: {
      fontSize: '0.72rem',
      fontWeight: 800,
      letterSpacing: '0.16em',
    },
    button: {
      fontWeight: 750,
    },
  },
  shape: {
    borderRadius: 10,
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          minWidth: 320,
          minHeight: '100vh',
          backgroundImage:
            'radial-gradient(circle at 78% 4%, rgba(165, 190, 166, 0.24), transparent 28rem)',
        },
      },
    },
    MuiButton: {
      defaultProps: {
        disableElevation: true,
      },
    },
  },
});

export default theme;
