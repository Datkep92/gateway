//  Cổng (wiki.qaq.gateway.switch) — giao diện gốc, không dùng WebView.
//
//  App là một bảng điều khiển: mở/tắt cổng, xem và sửa nhà cung cấp / key / model,
//  cài đặt, tự kiểm, sao lưu — đủ việc như trang quản lý web (trang web vẫn giữ
//  nguyên cho máy khác mở bằng trình duyệt).
#import <UIKit/UIKit.h>

extern NSString *const kProjectDir;      // thư mục dự án Gateway
extern NSString *const kFallbackURL;     // khi chưa đọc được cổng đang nghe

#pragma mark - tiện ích chung

UIColor *gwGreen(void);
UIColor *gwRed(void);
UIColor *gwAmber(void);
UIColor *gwDim(void);
UIImage *gwDotImage(NSInteger kind);     // 1 xanh, 2 đỏ, 3 vàng, 4 xám

NSString *gwTrim(NSString *s);
NSString *gwRun(NSString *script, NSArray<NSString *> *args);
NSString *gwCtl(NSArray<NSString *> *args);      // chạy gatewayctl
NSString *gwStateWord(NSString *ctlOutput);       // dang_chay | dang_treo | khong_chay | ""
NSString *gwStateNow(void);
NSString *gwAppKey(void);                         // client key riêng của app
void gwForgetKey(void);
void gwSetAppKey(NSString *k);                    // đổi key của app (sau khôi phục sao lưu)
void gwLog(NSString *fmt, ...);
NSString *gwStack(void);
NSString *gwAgo(double ts);
NSString *gwDur(double secs);
NSString *gwProviderLabel(NSString *name);        // tên đẹp của nhà cung cấp
NSArray<NSDictionary *> *gwKnownProviders(void);  // nhà cung cấp có thể thêm key
void gwBG(void (^work)(void));                    // việc nền
void gwMain(void (^work)(void));                  // về luồng chính
void gwOpenURL(NSString *url);

#pragma mark - máy khách HTTP

@interface GWA : NSObject
@property (nonatomic, copy) NSString *base;
@property (nonatomic, copy) NSString *key;
+ (instancetype)shared;
- (void)refresh;                                  // đọc lại địa chỉ + key
- (void)get:(NSString *)path query:(NSDictionary *)query done:(void (^)(id json, NSError *err))done;
- (void)send:(NSString *)method path:(NSString *)path body:(NSDictionary *)body
        done:(void (^)(id json, NSError *err))done;
@end

#pragma mark - hàng của bảng

@interface GWRow : NSObject
+ (instancetype)title:(NSString *)title sub:(NSString *)sub;
@property (nonatomic, copy) NSString *title;
@property (nonatomic, copy) NSString *sub;
@property (nonatomic, copy) NSString *value;       // chữ bên phải (khi không có công tắc)
@property (nonatomic, assign) NSInteger dotKind;   // 0 là không có chấm
@property (nonatomic, assign) BOOL chevron;
@property (nonatomic, assign) BOOL hasSwitch;
@property (nonatomic, assign) BOOL switchOn;
@property (nonatomic, copy, nullable) void (^tap)(GWRow *row);
@property (nonatomic, copy, nullable) void (^toggle)(BOOL on);
@property (nonatomic, strong, nullable) id extra;
@end

@interface GWSection : NSObject
+ (instancetype)header:(NSString *)header footer:(NSString *)footer;
@property (nonatomic, copy) NSString *header;
@property (nonatomic, copy) NSString *footer;
@property (nonatomic, strong) NSMutableArray<GWRow *> *rows;
@end

/** Bảng nền: lớp con chỉ cần dựng `sections` trong `loadData`. */
@interface GWTableVC : UITableViewController
@property (nonatomic, strong) NSMutableArray<GWSection *> *sections;
- (void)loadData;                                  // lớp con nạp dữ liệu
- (GWSection *)addSection:(NSString *)header footer:(NSString *)footer;
- (GWRow *)addRow:(GWSection *)section title:(NSString *)title sub:(NSString *)sub;
- (void)reload;                                    // nạp lại + vẽ lại
- (void)busy:(NSString *)text;                     // vòng xoay chờ
- (void)done;                                      // tắt vòng xoay
- (void)toast:(NSString *)text;
- (void)report:(NSError *)err title:(NSString *)title;
- (void)fail:(NSString *)text;                     // báo lỗi rồi tắt chờ
- (void)ask:(NSString *)title options:(NSArray<NSString *> *)options run:(void (^)(NSInteger i))run;
- (void)prompt:(NSString *)title value:(NSString *)value run:(void (^)(NSString *text))run;
- (void)push:(UIViewController *)vc;
@end

#pragma mark - hàng dùng chung

GWRow *gwKeyRow(NSDictionary *key);
void gwKeyTap(GWTableVC *vc, NSDictionary *key, void (^after)(void));
GWRow *gwModelRow(NSDictionary *m);
void gwModelSwitch(GWTableVC *vc, GWRow *row, NSDictionary *m, void (^after)(void));
void gwModelTap(GWTableVC *vc, NSDictionary *m, void (^after)(void));

#pragma mark - các màn hình

@interface HomeVC : GWTableVC @end
@interface LogsVC : GWTableVC @end
@interface ProvidersVC : GWTableVC @end
@interface ProviderDetailVC : GWTableVC
@property (nonatomic, copy) NSString *name;
@end
@interface KeysVC : GWTableVC @end
@interface KeyAddVC : GWTableVC
@property (nonatomic, copy) NSString *provider;    // rỗng thì người dùng tự chọn
@end
@interface ModelsVC : GWTableVC @end
@interface SettingsVC : GWTableVC @end
@interface BackupVC : GWTableVC @end
