const config = {
  host: '', //'http://192.168.1.106:8000',//default' , // 'http://localhost:8000',
  lang: 'en',
  siteName: 'learn100',
  maxUserCountInRoom: 300,
  demo_meet_id: 1,
  replace_server_sfu_url: false,

  AllowWebrtcConfig: true,

  // sfu_frameRate: 8, // value must be 5 ...  25
  // sfu_video_quality: 3, // value must be 1 ...  9
  //
  // sfu_share_screen_resolution: {
  //   width: 1600,
  //   height: 800,
  //   frameRate: 7,
  // },

  socketUseJson: false,
  ClientBuildVersion: 28,
};
window.config = config;
